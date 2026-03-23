"""Text sanitizer: replaces PII with deterministic tokens for safe AI sharing."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    import phonenumbers
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False

# Default storage location for token-to-PII mappings
_DEFAULT_MAPPINGS_PATH = Path("data/sanitizer/mappings.json")

# Countries for phone number detection (DT markets + common)
_PHONE_REGIONS = [
    "HR", "DE", "AT", "HU", "GR", "SK", "CZ", "PL", "ME", "MK",
    "US", "GB", "NL", "CH", "FR", "IT", "ES",
]

# Keywords that precede numbers which are NOT phone numbers
_PHONE_SKIP_RE = re.compile(
    r"(?:Meeting\s*ID|Besprechungs-ID|ID\s+sastanka|Passcode|PIN|"
    r"Conference\s*ID|Webinar\s*ID|Kennung|Zugangscode|"
    r"ONEX-|JIRA-|Ticket|Issue)\s*:?\s*$",
    re.IGNORECASE,
)

# How far back to look for phone-skip keywords (characters)
_PHONE_SKIP_LOOKBACK = 40


class TextSanitizer:
    """Replace PII (emails, phones, IPs) with deterministic tokens.

    Tokens are 4-char base36 strings derived from SHA-256 hashes.
    Same input always produces the same token. Mappings are persisted
    to a JSON file for reverse lookup (unsanitize).
    """

    # Email regex: handles standard, angle-bracket, plus-addressing, subdomains
    _EMAIL_RE = re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    )

    # Already-tokenized patterns to skip
    _TOKEN_RE = re.compile(r"\[(EMAIL|PHONE|IP)-[a-z0-9]{4}\]")

    # IPv4 address
    _IP_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

    # Version-string guard for IPs: v1.2.3.4, version 1.2.3
    _VERSION_PREFIX_RE = re.compile(r"(?:v(?:ersion)?\s*)$", re.IGNORECASE)

    # Boilerplate patterns
    _SEPARATOR_LINE_RE = re.compile(r"^[\s]*[*\-=]{10,}[\s]*$", re.MULTILINE)
    _CID_RE = re.compile(r"\[cid:[^\]]+\]", re.IGNORECASE)
    _EXCESS_BLANKS_RE = re.compile(r"\n{4,}")

    def __init__(self, mappings_path: str | Path | None = None) -> None:
        self._mappings_path = Path(mappings_path or _DEFAULT_MAPPINGS_PATH)
        self._mappings: dict = self._load_mappings()

    # ── Public API ──────────────────────────────────────────────

    def sanitize(
        self, text: str, *, clean_boilerplate: bool = True,
    ) -> tuple[str, dict]:
        """Replace PII with tokens. Returns (sanitized_text, stats).

        Stats keys: emails_replaced, phones_replaced, ips_replaced.
        """
        stats = {"emails_replaced": 0, "phones_replaced": 0, "ips_replaced": 0}

        if clean_boilerplate:
            text = self._clean_boilerplate(text)

        text, stats["emails_replaced"] = self._replace_emails(text)
        text, stats["phones_replaced"] = self._replace_phones(text)
        text, stats["ips_replaced"] = self._replace_ips(text)

        self._save_mappings()
        return text, stats

    def unsanitize(self, text: str) -> str:
        """Replace tokens back with original PII values."""
        def _replace_token(m: re.Match) -> str:
            token = m.group(0)               # e.g. [EMAIL-a1b2]
            token_key = token[1:-1]          # e.g. EMAIL-a1b2
            category = token_key.split("-")[0].lower() + "s"  # e.g. emails
            section = self._mappings.get(category, {})
            return section.get(token_key, token)

        return self._TOKEN_RE.sub(_replace_token, text)

    def lookup(self, token: str) -> str | None:
        """Look up a single token, return original value or None.

        Accepts both '[EMAIL-a1b2]' and 'EMAIL-a1b2' formats.
        """
        token_key = token.strip("[]")
        category = token_key.split("-")[0].lower() + "s"
        return self._mappings.get(category, {}).get(token_key)

    def get_all_mappings(self) -> dict:
        """Return a copy of all current mappings (for display)."""
        return {
            category: {
                k: v for k, v in entries.items()
                if not k.startswith(("EMAIL-", "PHONE-", "IP-"))
            }
            for category, entries in self._mappings.items()
            if category != "version"
        }

    # ── Token generation ────────────────────────────────────────

    @staticmethod
    def _make_token(value: str, prefix: str) -> str:
        """Generate a deterministic 4-char base36 token from a value."""
        digest = hashlib.sha256(value.encode()).hexdigest()
        # Convert first 8 hex chars to int, then to base36 (4 chars)
        num = int(digest[:8], 16)
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        result = []
        for _ in range(4):
            result.append(chars[num % 36])
            num //= 36
        token_id = "".join(result)
        return f"{prefix}-{token_id}"

    def _register(self, category: str, original: str, token_key: str) -> None:
        """Register a bidirectional mapping."""
        section = self._mappings.setdefault(category, {})
        section[original] = token_key
        section[token_key] = original

    # ── Email replacement ───────────────────────────────────────

    def _replace_emails(self, text: str) -> tuple[str, int]:
        count = 0
        # Collect all positions of existing tokens to skip
        token_spans = {(m.start(), m.end()) for m in self._TOKEN_RE.finditer(text)}

        def _sub_email(m: re.Match) -> str:
            nonlocal count
            # Skip if this match is inside an existing token
            for ts, te in token_spans:
                if m.start() >= ts and m.end() <= te:
                    return m.group(0)

            email = m.group(0).lower().strip()
            token_key = self._make_token(email, "EMAIL")
            self._register("emails", email, token_key)
            count += 1
            return f"[{token_key}]"

        text = self._EMAIL_RE.sub(_sub_email, text)
        return text, count

    # ── Phone replacement ───────────────────────────────────────

    def _replace_phones(self, text: str) -> tuple[str, int]:
        if not PHONENUMBERS_AVAILABLE:
            return text, 0

        count = 0
        replacements: list[tuple[int, int, str]] = []

        for region in _PHONE_REGIONS:
            for match in phonenumbers.PhoneNumberMatcher(text, region):
                start, end = match.start, match.end

                # Check for false-positive keywords before the number
                lookback_start = max(0, start - _PHONE_SKIP_LOOKBACK)
                preceding = text[lookback_start:start]
                if _PHONE_SKIP_RE.search(preceding):
                    continue

                # Skip if overlapping with an already-planned replacement
                if any(s <= start < e or s < end <= e for s, e, _ in replacements):
                    continue

                number = match.number
                e164 = phonenumbers.format_number(
                    number, phonenumbers.PhoneNumberFormat.E164,
                )
                token_key = self._make_token(e164, "PHONE")
                self._register("phones", e164, token_key)
                replacements.append((start, end, f"[{token_key}]"))
                count += 1

        # Apply replacements in reverse order to preserve positions
        for start, end, replacement in sorted(replacements, reverse=True):
            text = text[:start] + replacement + text[end:]

        return text, count

    # ── IP replacement ──────────────────────────────────────────

    def _replace_ips(self, text: str) -> tuple[str, int]:
        count = 0

        def _sub_ip(m: re.Match) -> str:
            nonlocal count
            ip = m.group(1)

            # Validate octets
            octets = ip.split(".")
            if any(int(o) > 255 for o in octets):
                return m.group(0)

            # Skip version strings: v1.2.3.4
            preceding_start = max(0, m.start() - 20)
            preceding = text[preceding_start:m.start()]
            if self._VERSION_PREFIX_RE.search(preceding):
                return m.group(0)

            token_key = self._make_token(ip, "IP")
            self._register("ips", ip, token_key)
            count += 1
            return f"[{token_key}]"

        text = self._IP_RE.sub(_sub_ip, text)
        return text, count

    # ── Boilerplate cleaning ────────────────────────────────────

    def _clean_boilerplate(self, text: str) -> str:
        """Strip generic boilerplate: separator lines, CID refs, excess whitespace."""
        text = self._SEPARATOR_LINE_RE.sub("", text)
        text = self._CID_RE.sub("", text)
        text = self._EXCESS_BLANKS_RE.sub("\n\n\n", text)
        # Strip trailing whitespace per line
        text = "\n".join(line.rstrip() for line in text.splitlines())
        return text.strip() + "\n"

    # ── Mappings persistence ────────────────────────────────────

    def _load_mappings(self) -> dict:
        """Load mappings from JSON file, or return empty structure."""
        if self._mappings_path.exists():
            with open(self._mappings_path) as f:
                return json.load(f)
        return {"version": 1, "emails": {}, "phones": {}, "ips": {}}

    def _save_mappings(self) -> None:
        """Save mappings to JSON file."""
        self._mappings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._mappings_path, "w") as f:
            json.dump(self._mappings, f, indent=2, ensure_ascii=False)
