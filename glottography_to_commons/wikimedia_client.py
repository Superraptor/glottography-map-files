"""
Wikimedia Commons API client for creating categories and uploading .map files.
Adheres strictly to Wikimedia API policies (User-Agent, maxlag, rate-limiting, bot passwords).
"""

import http.cookiejar
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from glottography_to_commons.config import Config

logger = logging.getLogger(__name__)


class WikimediaAPIError(Exception):
    """Raised when the MediaWiki API returns an error structure."""
    def __init__(self, code: str, info: str, raw: Optional[dict] = None):
        super().__init__(f"MediaWiki API error [{code}]: {info}")
        self.code = code
        self.info = info
        self.raw = raw or {}


class WikimediaClient:
    """Client for Wikimedia Commons MediaWiki Action API."""

    def __init__(self, config: Config):
        self.config = config
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.csrf_token: Optional[str] = None
        self.is_authenticated = False
        self._last_request_time = 0.0

    def _rate_limit_wait(self) -> None:
        """Enforce delay between edit requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.config.request_delay:
            wait_time = self.config.request_delay - elapsed
            logger.debug("Rate limiting: sleeping %.2f seconds", wait_time)
            time.sleep(wait_time)
        self._last_request_time = time.time()

    def _api_request(
        self,
        method: str,
        params: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
        retries: int = 5
    ) -> Dict[str, Any]:
        """
        Executes a MediaWiki API request with exponential backoff, maxlag handling,
        and User-Agent compliance.
        """
        self._rate_limit_wait()

        # Always request JSON format
        params = dict(params)
        params["format"] = "json"
        params["formatversion"] = "2"

        url = f"{self.config.commons_api_url}?{urllib.parse.urlencode(params)}"
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
        }

        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        for attempt in range(1, retries + 1):
            try:
                with self.opener.open(req, timeout=45) as resp:
                    resp_bytes = resp.read()
                    resp_json = json.loads(resp_bytes.decode("utf-8"))

                    # Handle MediaWiki maxlag error
                    if "error" in resp_json:
                        err = resp_json["error"]
                        code = err.get("code", "unknown")
                        info = err.get("info", "")

                        if code == "maxlag":
                            wait = 5.0
                            # Try to extract suggested wait from headers or response
                            logger.warning("Wikimedia maxlag hit. Backing off for %.1f seconds (attempt %d/%d)", wait, attempt, retries)
                            time.sleep(wait)
                            continue

                        if code in ("readonly", "ratelimited") and attempt < retries:
                            wait = 2.0 ** attempt
                            logger.warning("MediaWiki [%s]: %s. Backing off %.1f seconds", code, info, wait)
                            time.sleep(wait)
                            continue

                        raise WikimediaAPIError(code, info, resp_json)

                    return resp_json

            except urllib.error.HTTPError as e:
                logger.warning("HTTP %d from Commons API (attempt %d/%d)", e.code, attempt, retries)
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else (2.0 ** attempt)
                    time.sleep(wait)
                else:
                    raise

            except (urllib.error.URLError, TimeoutError) as e:
                logger.warning("Network error contacting Commons API: %s (attempt %d/%d)", e, attempt, retries)
                if attempt < retries:
                    time.sleep(2.0 ** attempt)
                else:
                    raise

        raise RuntimeError(f"Failed Wikimedia API request after {retries} retries: {url}")

    def login(self) -> bool:
        """
        Authenticate using MediaWiki Bot Passwords.
        Returns True on successful login.
        """
        if self.config.dry_run:
            logger.info("[DRY RUN] Skipping Wikimedia login.")
            self.is_authenticated = True
            return True

        if not self.config.commons_username or not self.config.commons_bot_password:
            logger.warning(
                "COMMONS_USERNAME or COMMONS_BOT_PASSWORD not configured. "
                "Running in unauthenticated / read-only mode."
            )
            return False

        logger.info("Logging into Wikimedia Commons as %s...", self.config.commons_username)

        # 1. Get login token
        res = self._api_request("GET", {"action": "query", "meta": "tokens", "type": "login"})
        login_token = res.get("query", {}).get("tokens", {}).get("logintoken")
        if not login_token:
            raise RuntimeError("Failed to retrieve login token from Wikimedia Commons")

        # 2. Execute login
        login_data = {
            "action": "login",
            "lgname": self.config.commons_username,
            "lgpassword": self.config.commons_bot_password,
            "lgtoken": login_token,
        }
        login_res = self._api_request("POST", {"action": "login"}, data=login_data)
        login_status = login_res.get("login", {}).get("result")

        if login_status != "Success":
            reason = login_res.get("login", {}).get("reason", login_status)
            raise WikimediaAPIError("login_failed", f"Failed to log into Wikimedia Commons: {reason}", login_res)

        logger.info("Successfully authenticated to Wikimedia Commons!")
        self.is_authenticated = True

        # 3. Post-login verification: verify user rights, block status, and talk page messages
        userinfo_res = self._api_request("GET", {
            "action": "query",
            "meta": "userinfo",
            "uiprop": "rights|groups|blockinfo|hasmsg|messages"
        })
        uinfo = userinfo_res.get("query", {}).get("userinfo", {})
        actual_name = uinfo.get("name")
        logger.info("Confirmed identity on Wikimedia Commons: %s (id: %s)", actual_name, uinfo.get("id"))

        # Verify user matches expected bot account
        expected_base = self.config.commons_username.split("@")[0].strip()
        if actual_name and actual_name.lower() != expected_base.lower():
            logger.warning("Logged in user '%s' differs from expected base '%s'", actual_name, expected_base)

        # Check 1: Is user or IP blocked?
        if uinfo.get("blockid") or "blocked" in uinfo or uinfo.get("blockinfo"):
            block_reason = uinfo.get("blockreason", "Unknown block reason")
            raise RuntimeError(f"CRITICAL SAFETY HALT: Account {actual_name} is BLOCKED on Wikimedia Commons ({block_reason})! Stopping immediately.")

        # Check 2: Emergency shutoff via talk page messages
        if uinfo.get("messages") or uinfo.get("hasmsg"):
            raise RuntimeError(f"CRITICAL SAFETY HALT: Bot talk page for {actual_name} has unread messages/alerts! Emergency shutoff triggered. Stopping immediately.")

        # Check 3: Edit permissions
        rights = uinfo.get("rights", [])
        if "edit" not in rights:
            raise RuntimeError(f"CRITICAL SAFETY HALT: Bot account {actual_name} lacks 'edit' rights on Wikimedia Commons! Rights: {rights}")

        # 4. Refresh CSRF token after login
        self.get_csrf_token(force_refresh=True)
        return True

    def get_csrf_token(self, force_refresh: bool = False) -> str:
        """Retrieve and cache a CSRF token for editing."""
        if self.csrf_token and not force_refresh:
            return self.csrf_token

        if self.config.dry_run:
            self.csrf_token = "dry-run-csrf-token"
            return self.csrf_token

        res = self._api_request("GET", {"action": "query", "meta": "tokens", "type": "csrf"})
        token = res.get("query", {}).get("tokens", {}).get("csrftoken")
        if not token:
            raise RuntimeError("Failed to retrieve CSRF token from Wikimedia Commons")

        self.csrf_token = token
        return self.csrf_token

    def page_exists(self, title: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a page exists on Commons.
        Returns (exists: bool, content: Optional[str]).
        """
        if self.config.dry_run:
            return False, None

        params = {
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
        }
        res = self._api_request("GET", params)
        pages = res.get("query", {}).get("pages", [])
        if not pages:
            return False, None

        page = pages[0]
        if page.get("missing", False) or page.get("pageid", 0) <= 0:
            return False, None

        revs = page.get("revisions", [])
        if revs:
            slots = revs[0].get("slots", {})
            content = slots.get("main", {}).get("content")
            return True, content

        return True, None

    def ensure_category(self) -> bool:
        """
        Checks whether the target category ('Category:Map data of languages') exists on Wikimedia Commons.
        If not, creates it as a subcategory of 'Category:Map data by object'.
        """
        cat_title = f"Category:{self.config.category_name}"
        logger.info("Checking existence of category: %s", cat_title)

        if self.config.dry_run:
            logger.info("[DRY RUN] Would check and ensure category: %s", cat_title)
            return True

        exists, _ = self.page_exists(cat_title)
        if exists:
            logger.info("Category %s already exists on Commons.", cat_title)
            return True

        # Wikitext definition
        wikitext = (
            "{{CategoryMapData}}\n"
            "Map data files (.map) in the Data: namespace representing geographical "
            "speaker areas and distribution boundaries for the world's languages.\n\n"
            f"[[Category:{self.config.parent_category}|{self.config.parent_sortkey}]]\n"
        )

        summary = (
            f"Creating subcategory [[Category:{self.config.category_name}]] "
            f"under [[Category:{self.config.parent_category}]] for Glottography language maps"
        )

        if self.config.dry_run:
            logger.info("[DRY RUN] Would create category: %s with wikitext:\n%s", cat_title, wikitext)
            return True

        logger.info("Creating category page %s...", cat_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": cat_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }

        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        result = res.get("edit", {}).get("result")
        if result == "Success":
            logger.info("Successfully created %s on Wikimedia Commons!", cat_title)
            return True
        else:
            raise RuntimeError(f"CRITICAL SAFETY HALT: Failed creating category {cat_title}: {res}")

    def ensure_talk_page(self, page_title: str, language_name: str) -> bool:
        """
        Categorizes the Data talk: page under 'Category:Map data of languages'.
        """
        from glottography_to_commons.utils import get_language_sort_key
        talk_title = page_title.replace("Data:", "Data talk:")
        sort_key = get_language_sort_key(language_name) if language_name else ""
        wikitext = f"[[Category:{self.config.talk_category}|{sort_key}]]"
        summary = f"Categorizing map data under Category:{self.config.talk_category}"

        if self.config.dry_run:
            logger.info("[DRY RUN] Would ensure talk page %s with content: %s", talk_title, wikitext)
            return True

        exists, current_text = self.page_exists(talk_title)
        if exists and current_text and f"Category:{self.config.talk_category}" in current_text:
            return True

        logger.info("Setting talk page category for %s...", talk_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": talk_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }
        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        if res.get("edit", {}).get("result") != "Success":
            raise RuntimeError(f"CRITICAL SAFETY HALT: Failed editing talk page {talk_title}: {res}")
        return True

    def ensure_language_category(self, language_name: str) -> bool:
        """
        Ensures 'Category:Linguistic maps of the <Language> language' exists under
        'Category:Linguistic maps by language mapped'.
        """
        from glottography_to_commons.map_builder import MapBuilder
        from glottography_to_commons.utils import get_language_sort_key
        lang_cat = MapBuilder.get_language_map_category(language_name)
        cat_title = f"Category:{lang_cat}"
        sort_key = get_language_sort_key(language_name)
        wikitext = f"[[Category:{self.config.parent_linguistic_category}|{sort_key}]]\n"
        summary = f"Creating linguistic map subcategory for {sort_key} under [[Category:{self.config.parent_linguistic_category}]]"

        if self.config.dry_run:
            logger.info("[DRY RUN] Would ensure language category %s with content: %s", cat_title, wikitext.strip())
            return True

        exists, _ = self.page_exists(cat_title)
        if exists:
            return True

        logger.info("Creating language category page %s...", cat_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": cat_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }
        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        if res.get("edit", {}).get("result") != "Success":
            raise RuntimeError(f"CRITICAL SAFETY HALT: Failed creating language category {cat_title}: {res}")
        return True

    def build_bot_userpage_wikitext(self) -> str:
        """
        Generates standard Wikimedia Commons Bot User Page documentation
        conforming to Commons:Bots and MediaWiki bot policy.
        """
        edits_per_min = int(60.0 / max(self.config.request_delay, 1.0))
        lines = [
            "{{Bot|operator=" + self.config.bot_operator + "}}",
            "",
            "== Bot Information ==",
            f"* '''Operator''': [[User:{self.config.bot_operator}|{self.config.bot_operator}]] (contact via [[User talk:{self.config.bot_operator}|talk page]] or [https://github.com/Superraptor/glottography-map-files/issues repository issues])",
            f"* '''Tasks''': Ingests peer-reviewed geographical language distribution datasets from the [https://zenodo.org/communities/glottography Zenodo Glottography community] into the Commons <code>Data:</code> namespace as standardized <code>.map</code> files (JsonConfig) with full attribution, provenance (DOIs, Glottolog identifiers, ISO 639-3 codes, cartographers, CC-BY-4.0 licenses), categorizes maps into [[Category:Linguistic maps by language mapped]], and catalogs discussion pages in [[Category:Map data of languages]].",
            f"* '''Operation''': Manually assisted and supervised batch execution with SQLite state tracking and automated JSON schema validation.",
            f"* '''When''': Intermittent (executed during off-peak hours or in scheduled supervised batches).",
            f"* '''Maximum edit rate''': ~{edits_per_min} edits per minute (delay of {self.config.request_delay}s between edits; strictly enforces <code>maxlag={self.config.maxlag}</code> and exponential backoff).",
            f"* '''Language''': Python 3.11 ([https://github.com/Superraptor/glottography-map-files glottography-map-files]).",
            "",
            "== Technical Compliance & Safety ==",
            f"* '''Emergency Shutoff''': Halts immediately if blocked, if talk page messages are received, or on consecutive HTTP 429/503 server errors.",
            f"* '''Maxlag Throttling''': Respects Wikimedia server load with <code>maxlag={self.config.maxlag}</code>.",
            f"* '''Namespaces''': Restricts write operations strictly to <code>Data:</code> (Map.JsonConfig), <code>Data talk:</code>, and <code>Category:</code>.",
            f"* '''Attribution & Provenance''': Every map includes author/cartographer citation, Zenodo record DOI, Glottolog ID, ISO 639-3 code, and CC-BY-4.0 license.",
            "",
            "[[Category:Commons bots]]",
        ]
        return "\n".join(lines)

    def ensure_bot_userpage(self) -> bool:
        """
        Ensures the bot user page exists on Commons with standard bot documentation.
        """
        if not self.config.commons_username:
            return False
        bot_username = self.config.commons_username.split("@")[0]
        userpage_title = f"User:{bot_username}"
        wikitext = self.build_bot_userpage_wikitext()
        summary = "Creating/updating bot user page complying with Commons:Bots policy"

        if self.config.dry_run:
            logger.info("[DRY RUN] Would ensure bot user page %s", userpage_title)
            return True

        exists, _ = self.page_exists(userpage_title)
        if exists:
            return True

        logger.info("Creating bot user page %s...", userpage_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": userpage_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }
        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        if res.get("edit", {}).get("result") != "Success":
            raise RuntimeError(f"CRITICAL SAFETY HALT: Failed creating bot user page {userpage_title}: {res}")
        return True

    @staticmethod
    def validate_map_payload(page_title: str, content_json: str) -> None:
        """
        Ultra-strict validation of .map file and page title prior to upload.
        Raises ValueError or RuntimeError immediately if anything looks suspicious.
        """
        # 1. Page title checks
        if not page_title.startswith("Data:"):
            raise ValueError(f"Security check failed: page title '{page_title}' must start with 'Data:' namespace")
        if not page_title.endswith(".map"):
            raise ValueError(f"Security check failed: page title '{page_title}' must end with '.map' extension")
        if len(page_title) > 240:
            raise ValueError(f"Page title exceeds safe MediaWiki limit of 240 chars: {len(page_title)} chars")
        forbidden_chars = set('#<>[]|{}')
        for ch in page_title.replace("Data:", ""):
            if ch in forbidden_chars:
                raise ValueError(f"Page title contains forbidden MediaWiki character '{ch}': {page_title}")

        # 2. Byte size checks
        raw_bytes = content_json.encode("utf-8")
        if len(raw_bytes) > 1_900_000:
            raise ValueError(f"Payload size ({len(raw_bytes)} bytes) exceeds safe Commons threshold of 1.9 MB")
        if len(raw_bytes) < 100:
            raise ValueError(f"Payload size ({len(raw_bytes)} bytes) is suspiciously small")

        # 3. JSON parse checks
        try:
            data = json.loads(content_json)
        except Exception as e:
            raise ValueError(f"Invalid JSON payload: {e}")

        # 4. Forbidden fields check
        if "version" in data:
            raise ValueError("Schema violation: Map.JsonConfig forbids top-level 'version' attribute")

        # 5. Required fields
        for req_key in ("license", "description", "sources", "mediawikiCategories", "data"):
            if req_key not in data:
                raise ValueError(f"Schema violation: missing required field '{req_key}'")

        # 6. License check
        valid_licenses = {"CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0", "CC0-1.0"}
        if data["license"] not in valid_licenses:
            raise ValueError(f"Suspicious license: '{data['license']}' not in approved set: {valid_licenses}")

        # 7. Description check
        desc = data["description"]
        if not isinstance(desc, dict) or "en" not in desc or not desc["en"].strip():
            raise ValueError("Schema violation: description must be a dict with non-empty 'en' string")

        # 8. Sources check
        sources = data["sources"]
        if not isinstance(sources, str) or len(sources.strip()) < 30:
            raise ValueError("Schema violation: sources wikitext is suspiciously short or empty")
        if "zenodo" not in sources.lower() and "doi" not in sources.lower():
            raise ValueError("Provenance check failed: sources wikitext does not cite Zenodo/DOI")

        # 9. Categories check
        cats = data["mediawikiCategories"]
        if not isinstance(cats, list) or len(cats) == 0:
            raise ValueError("Schema violation: mediawikiCategories must be a non-empty list")
        for cat in cats:
            if not isinstance(cat, dict) or "name" not in cat or "sort" not in cat:
                raise ValueError(f"Schema violation: category entry must have 'name' and 'sort': {cat}")
            cat_name = cat["name"]
            if not cat_name.startswith("Linguistic maps of ") and cat_name != "Linguistic maps by language mapped":
                raise ValueError(f"Suspicious category name in mediawikiCategories: '{cat_name}'")
            if not str(cat["sort"]).strip():
                raise ValueError(f"Category sort key is empty in: {cat}")

        # 9b. Optional zoom, latitude, longitude check (Kartographer map framing)
        if "zoom" in data:
            if not isinstance(data["zoom"], int) or not (0 <= data["zoom"] <= 18):
                raise ValueError(f"Schema violation: 'zoom' must be an integer between 0 and 18, got: {data['zoom']}")
        if "latitude" in data:
            if not isinstance(data["latitude"], (int, float)) or not (-90.0 <= data["latitude"] <= 90.0):
                raise ValueError(f"Schema violation: 'latitude' must be between -90 and 90, got: {data['latitude']}")
        if "longitude" in data:
            if not isinstance(data["longitude"], (int, float)) or not (-180.0 <= data["longitude"] <= 180.0):
                raise ValueError(f"Schema violation: 'longitude' must be between -180 and 180, got: {data['longitude']}")

        # 10. GeoJSON Data check
        geojson = data["data"]
        if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
            raise ValueError("Schema violation: 'data' must be a GeoJSON FeatureCollection")
        features = geojson.get("features")
        if not isinstance(features, list) or len(features) == 0:
            raise ValueError("Schema violation: 'features' must be a non-empty list")

        for f_idx, feat in enumerate(features):
            if not isinstance(feat, dict) or feat.get("type") != "Feature":
                raise ValueError(f"Feature [{f_idx}] is not a valid GeoJSON Feature")
            geom = feat.get("geometry")
            if not isinstance(geom, dict) or "type" not in geom or "coordinates" not in geom:
                raise ValueError(f"Feature [{f_idx}] has invalid geometry")

            def check_coords(c, path=""):
                if isinstance(c, (int, float)):
                    return
                elif isinstance(c, list):
                    if len(c) == 2 and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float)):
                        lon, lat = c[0], c[1]
                        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                            raise ValueError(f"Coordinate out of WGS84 bounds at {path}: lon={lon}, lat={lat}")
                    else:
                        for idx, sub in enumerate(c):
                            check_coords(sub, f"{path}[{idx}]")
                else:
                    raise ValueError(f"Suspicious coordinate value {type(c)} at {path}")

            check_coords(geom["coordinates"], f"features[{f_idx}]")

    def upload_map(
        self,
        page_title: str,
        content_json: str,
        summary: str,
        language_name: str = "",
        recreate: bool = False
    ) -> Dict[str, Any]:
        """
        Edits / creates a .map page in the Data: namespace on Wikimedia Commons.
        Performs ultra-strict pre-flight sanity checks and halts immediately on any anomaly.
        """
        # 1. Ultra-strict schema and geometry validation before touching the network
        self.validate_map_payload(page_title, content_json)

        if self.config.dry_run:
            logger.info("[DRY RUN] Passed validation. Would upload map to %s (summary: %s)", page_title, summary)
            if language_name:
                self.ensure_language_category(language_name)
                self.ensure_talk_page(page_title, language_name)
            return {
                "result": "Success",
                "page_title": page_title,
                "url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_title)}",
                "dry_run": True
            }

        # 2. Check if page already exists on Commons
        exists, existing_content = self.page_exists(page_title)
        if exists:
            if existing_content and existing_content.strip() == content_json.strip():
                logger.info("Page %s already exists with identical content. Safely skipping.", page_title)
                return {
                    "result": "Skipped",
                    "page_title": page_title,
                    "url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_title)}",
                    "reason": "identical_content"
                }
            elif not recreate:
                raise RuntimeError(
                    f"CRITICAL SAFETY HALT: Target page {page_title} already exists on Wikimedia Commons! "
                    "Refusing to overwrite existing community content to prevent unintended modifications."
                )

        logger.info("Uploading verified map data to %s...", page_title)
        token = self.get_csrf_token()

        edit_data = {
            "action": "edit",
            "title": page_title,
            "text": content_json,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }

        res = self._api_request("POST", {"action": "edit"}, data=edit_data)

        # 3. Check for any warnings or abnormal flags
        if "warnings" in res:
            raise RuntimeError(f"CRITICAL SAFETY HALT: Warnings returned by MediaWiki API for {page_title}: {res['warnings']}")

        edit_info = res.get("edit", {})

        if "warning" in edit_info:
            raise RuntimeError(f"CRITICAL SAFETY HALT: Edit warning returned by MediaWiki API for {page_title}: {edit_info['warning']}")
        if "spamblacklist" in edit_info:
            raise RuntimeError(f"CRITICAL SAFETY HALT: Spam blacklist triggered for {page_title}: {edit_info['spamblacklist']}")
        if "abusefilter" in edit_info:
            raise RuntimeError(f"CRITICAL SAFETY HALT: AbuseFilter triggered for {page_title}: {edit_info['abusefilter']}")
        if "captcha" in edit_info:
            raise RuntimeError(f"CRITICAL SAFETY HALT: CAPTCHA encountered for {page_title}! Halting immediately.")
        if edit_info.get("result") != "Success":
            raise WikimediaAPIError("upload_failed", f"Failed uploading {page_title}: {res}", res)

        logger.info("Successfully uploaded %s to Commons (rev %s)", page_title, edit_info.get("newrevid"))

        if language_name:
            try:
                self.ensure_language_category(language_name)
                self.ensure_talk_page(page_title, language_name)
            except Exception as cat_err:
                logger.error("Error setting categories/talk page for %s: %s", page_title, cat_err)
                raise

        return {
            "result": "Success",
            "page_title": page_title,
            "newrevid": edit_info.get("newrevid"),
            "url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_title)}",
        }
