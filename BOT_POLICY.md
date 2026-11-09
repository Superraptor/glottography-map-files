# Wikimedia Commons & MediaWiki Bot Policy Compliance

This document defines the bot operational parameters, safety controls, and user page specification for automated or semi-automated contributions to **Wikimedia Commons** in accordance with:
* [Wikimedia Commons Bot Policy (`Commons:Bots`)](https://commons.wikimedia.org/wiki/Commons:Bots)
* [MediaWiki Bot Policy](https://www.mediawiki.org/wiki/Manual:Bots)
* [Wikimedia User-Agent Policy](https://meta.wikimedia.org/wiki/User-Agent_policy)

---

## 1. Bot User Page Specification (`User:<BotName>`)

When operating an automated or assisted bot account on Wikimedia Commons, the user page (e.g. `User:GlottographyBot`) must contain the following information and standard `{{Bot}}` template:

```wikitext
{{Bot|operator=Superraptor}}

== Bot Information ==
* '''Operator''': [[User:Superraptor|Superraptor]] (contact: [[User talk:Superraptor|User talk:Superraptor]] or via [https://github.com/Superraptor/glottography-map-files/issues GitHub Issues])
* '''Tasks''': Ingests peer-reviewed geographical language distribution datasets from the [https://zenodo.org/communities/glottography Zenodo Glottography community] into the Commons <code>Data:</code> namespace as standardized <code>.map</code> files (JsonConfig) with full attribution, provenance (DOIs, Glottolog identifiers, ISO 639-3 codes, cartographers, CC-BY-4.0 licenses), categorizes maps into [[Category:Linguistic maps by language mapped]], and catalogs discussion pages in [[Category:Map data of languages]].
* '''Operation''': Manually assisted and supervised batch execution with SQLite state tracking and automated JSON schema validation.
* '''When''': Intermittent (executed during off-peak hours or in scheduled supervised batches).
* '''Maximum edit rate''': ~12 to 30 edits per minute (delay of 2.0 to 5.0 seconds between edits; strictly enforces <code>maxlag=5</code> and exponential backoff).
* '''Language''': Python 3.11 ([https://github.com/Superraptor/glottography-map-files glottography-map-files]).

== Technical Compliance & Safety ==
* '''Emergency Shutoff''': Halts immediately if blocked, if talk page messages are received, or on consecutive HTTP 429/503 server errors.
* '''Maxlag Throttling''': Respects Wikimedia server load with <code>maxlag=5</code>.
* '''Namespaces''': Restricts write operations strictly to <code>Data:</code> (Map.JsonConfig), <code>Data talk:</code>, and <code>Category:</code>.
* '''Attribution & Provenance''': Every map includes author/cartographer citation, Zenodo record DOI, Glottolog ID, ISO 639-3 code, and CC-BY-4.0 license.

[[Category:Commons bots]]
```

---

## 2. Policy Alignment Details

### Operator
* **Primary Operator**: `[[User:Superraptor|Superraptor]]`
* **Contact Channels**:
  * Wikimedia Commons User Talk: [User talk:Superraptor](https://commons.wikimedia.org/wiki/User_talk:Superraptor)
  * Public Issue Tracker: [GitHub Issues](https://github.com/Superraptor/glottography-map-files/issues)
* The operator actively monitors Commons talk pages and email notifications while the bot is active.

### Tasks
1. **Language Map Data Ingestion (`Data:*.map`)**:
   - Parses Cross-Linguistic Data Format (CLDF) and GeoJSON datasets published by the Glottography community on Zenodo.
   - Converts multi-polygon language speaker area distributions into schema-compliant `Map.JsonConfig` pages.
   - Enforces coordinate rounding ($\sim 1.1\,\text{m}$ precision) and payload limits ($<2\,\text{MB}$).
2. **Linguistic Categorization**:
   - Injects `mediawikiCategories` pointing to `Category:Linguistic maps of the <Language> language` sorted by the name of the language.
   - Creates missing language categories under parent [Category:Linguistic maps by language mapped](https://commons.wikimedia.org/wiki/Category:Linguistic_maps_by_language_mapped).
3. **Data Talk Page Indexing**:
   - Categorizes corresponding discussion pages (`Data talk:*.map`) under [Category:Map data of languages](https://commons.wikimedia.org/wiki/Category:Map_data_of_languages) with the language sort key.

### Operation
* **Mode**: Supervised and manually assisted batch operation.
* Every dataset is parsed locally, cross-checked against canonical CLDF language tables, and indexed in a local SQLite database (`data/pipeline_state.db`) before any upload attempt.
* An interactive HTML preview portal (`data/export/index.html`) is generated for human review.

### When
* **Schedule**: Intermittent execution.
* The bot is invoked only when new datasets or revisions are released on Zenodo, run in supervised batches primarily during off-peak Wikimedia server hours.

### Maximum Edit Rate & Throttling
* **Default Rate**: 1 request every 2.0 to 5.0 seconds ($\le 12\text{--}30\text{ edits/min}$).
* **Maxlag Enforcement**: Every MediaWiki API request includes `maxlag=5`. If Wikimedia database replication lag exceeds 5 seconds, the bot backs off exponentially.
* **HTTP 429 / 503 Handling**: Exponential backoff with up to 5 retries.

### Language & Technology Stack
* **Language**: Python 3.11.
* **Core Libraries**: `urllib`, `requests`, `sqlite3`, `json`.
* **Repository**: Open-source at [https://github.com/Superraptor/glottography-map-files](https://github.com/Superraptor/glottography-map-files).

---

## 3. Template for Bot Flag Request (`Commons:Bots/Requests`)

If seeking a formal bot flag on Wikimedia Commons, use the following submission template:

```wikitext
=== GlottographyCommonsBot ===
* '''Operator''': [[User:Superraptor|Superraptor]]
* '''Tasks''': Ingesting Glottography geolinguistic speaker area datasets from Zenodo into Data:*.map (JsonConfig) categorized under [[Category:Linguistic maps by language mapped]] and [[Category:Map data of languages]]
* '''Operation''': Manually assisted / supervised batch runs
* '''When''': Intermittent
* '''Maximum edit rate''': 12-20 edits per minute (maxlag=5)
* '''Language''': Python 3.11 (https://github.com/Superraptor/glottography-map-files)
* '''Sample edits''':
** [[:Data:Glottography Northern Ju (vase1234) - glottography dataset deri.map]]
** [[:Data:Glottography Bahamas Creole English (baha1260) - glottography dataset deri.map]]
* '''Bot User Page''': [[User:GlottographyCommonsBot]]
```

