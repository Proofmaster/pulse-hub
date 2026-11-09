"""Client profiles: one JSON file per client describes who they are and where to look.

Profiles live in the PRIVATE data repo (`clients/<slug>.json`); the public code repo only ships
`examples/client.example.json`. Files whose name starts with "_" are ignored (templates/drafts).
See README.md for every field.
"""
import json
import re
from pathlib import Path

from .text import BrandMatcher

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SOURCE_NAMES = ("web", "serper", "google_alerts", "f5bot", "linkedin")
ALERT_DEFAULT = ["mention", "Prime Target"]


class ProfileError(ValueError):
    pass


def _str_list(value, field, required=False):
    if value is None:
        value = []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ProfileError(f"'{field}' must be a list of strings")
    value = [v.strip() for v in value if v.strip()]
    if required and not value:
        raise ProfileError(f"'{field}' must not be empty")
    return value


class Client:
    def __init__(self, slug, data):
        if not SLUG.match(slug or ""):
            raise ProfileError(f"bad client slug {slug!r}: use lowercase letters, digits and dashes")
        if not isinstance(data, dict):
            raise ProfileError("profile must be a JSON object")
        self.slug = slug
        self.raw = data
        self.name = str(data.get("name") or "").strip()
        if not self.name:
            raise ProfileError("'name' is required")
        self.active = bool(data.get("active", True))
        self.website = str(data.get("website") or "")
        self.description = str(data.get("description") or "").strip()
        self.audience = str(data.get("audience") or "").strip()
        self.tone = str(data.get("tone") or "helpful and genuinely useful, never salesy").strip()

        brand = data.get("brand") or {}
        if not isinstance(brand, dict):
            raise ProfileError("'brand' must be an object")
        self.brand_terms = _str_list(brand.get("terms"), "brand.terms", required=True)
        self.ambiguous_terms = _str_list(brand.get("ambiguous_terms"), "brand.ambiguous_terms")
        self.not_after = _str_list(brand.get("ambiguous_not_after"), "brand.ambiguous_not_after")
        not_before = brand.get("ambiguous_not_before")
        self.not_before = None if not_before is None else _str_list(not_before, "brand.ambiguous_not_before")
        self.false_positives = _str_list(brand.get("false_positives"), "brand.false_positives")
        self.brand_note = str(brand.get("note") or "").strip()
        self.is_mention = BrandMatcher(self.brand_terms, self.ambiguous_terms, self.not_before,
                                       self.not_after, self.false_positives)

        self.can = _str_list(data.get("can"), "can", required=True)
        self.cannot = _str_list(data.get("cannot"), "cannot")
        self.competitors = _str_list(data.get("competitors"), "competitors")
        self.prompt_rules = _str_list(data.get("prompt_rules"), "prompt_rules")

        self.examples = []
        for ex in data.get("examples") or []:
            if not isinstance(ex, dict) or not ex.get("text") or not ex.get("category"):
                raise ProfileError("each example needs 'text' and 'category'")
            self.examples.append({"text": str(ex["text"]), "category": str(ex["category"])})

        queries = data.get("queries") or {}
        self.queries = {"brand": _str_list(queries.get("brand"), "queries.brand"),
                        "intent": _str_list(queries.get("intent"), "queries.intent")}

        sources = data.get("sources") or {}
        if not isinstance(sources, dict):
            raise ProfileError("'sources' must be an object")
        unknown = set(sources) - set(SOURCE_NAMES)
        if unknown:
            raise ProfileError(f"unknown source(s) {sorted(unknown)}; known: {list(SOURCE_NAMES)}")
        self.sources = {}
        for name in SOURCE_NAMES:
            cfg = sources.get(name) or {}
            if not isinstance(cfg, dict):
                raise ProfileError(f"sources.{name} must be an object")
            self.sources[name] = dict(cfg)
        for feed in self.sources["google_alerts"].get("feeds") or []:
            if not isinstance(feed, dict) or not str(feed.get("url", "")).startswith("http"):
                raise ProfileError("each sources.google_alerts.feeds entry needs a 'url'")
            if feed.get("kind", "intent") not in ("brand", "intent"):
                raise ProfileError("google_alerts feed 'kind' must be 'brand' or 'intent'")

        slack = data.get("slack") or {}
        # Env var for local runs only; in GitHub Actions the webhook comes from SLACK_WEBHOOKS[slug].
        self.slack_secret = str(slack.get("webhook_env") or f"SLACK_WEBHOOK_{slug.upper().replace('-', '_')}")
        self.alert_on = _str_list(slack.get("alert_on", ALERT_DEFAULT), "slack.alert_on")

    # ---- helpers -------------------------------------------------------
    def source(self, name):
        return self.sources.get(name, {})

    def enabled(self, name):
        return bool(self.source(name).get("enabled"))

    def queries_for(self, source_name, kind):
        """A source with its own `queries` block uses only that block (a missing kind = none, so e.g. a paid
        source never silently inherits the long free-source list); otherwise the profile-level `queries`."""
        own = self.source(source_name).get("queries")
        if isinstance(own, dict):
            return _str_list(own.get(kind), f"sources.{source_name}.queries.{kind}")
        return list(self.queries.get(kind, []))

    def f5bot_keywords(self):
        kws = self.source("f5bot").get("keywords")
        return _str_list(kws, "sources.f5bot.keywords") if kws else self.brand_terms + self.ambiguous_terms

    def __repr__(self):
        return f"Client({self.slug!r})"


def load_client(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProfileError(f"{path.name}: invalid JSON ({e})") from e
    try:
        return Client(path.stem, data)
    except ProfileError as e:
        raise ProfileError(f"{path.name}: {e}") from e


def load_clients(directory, only=None, include_inactive=False, errors=None):
    """Active clients (or exactly `only`). With an `errors` list, a broken profile is recorded there and
    skipped instead of raising, so one bad file never stops every other client's scheduled run."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ProfileError(f"clients directory not found: {directory}")
    clients = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            client = load_client(path)
        except ProfileError as e:
            if errors is None:
                raise
            errors.append(str(e))
            continue
        if only and client.slug not in only:
            continue
        if client.active or include_inactive or only:
            clients.append(client)
    if only:
        missing = set(only) - {c.slug for c in clients}
        if missing:
            raise ProfileError(f"no profile for: {sorted(missing)}")
    return clients
