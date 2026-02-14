# FEC Campaign Finance — JSON API Reference

Base URL: `http://localhost:5000`

All endpoints accept `GET` requests and return JSON. No authentication required (local network only).

## Common Patterns

**Pagination:** Most endpoints support `page` (1-indexed, default 1). Responses include `page`, `total_pages`, and `total_results`.

**Page size:** 50 results per page (contributions, contributors, recipients).

**Sorting:** Use `sort_by` and `order` parameters where applicable. `order` accepts `asc` or `desc`.

**Error responses:**
```json
{"error": "Description of the problem"}
```
Returned with HTTP 400 for bad parameters.

---

## Endpoints

### 1. `GET /api/search` — Search Contributions

Search individual campaign contributions with cascading filter logic.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `first_name` | string | No* | | Contributor first name (case-insensitive) |
| `last_name` | string | No* | | Contributor last name (case-insensitive) |
| `city` | string | No | | Contributor city |
| `state` | string | No | | Two-letter state code |
| `zip_code` | string | No | | ZIP code (prefix matching) |
| `year` | string | No | | 4-digit year filter |
| `sort_by` | string | No | `contribution_date` | `contribution_date` or `amount` |
| `order` | string | No | `desc` | `asc` or `desc` |
| `page` | int | No | 1 | Page number |

*At least one search parameter is required.

**Cascading logic:** If the initial search returns no results, filters are automatically relaxed:
1. All filters applied
2. ZIP code dropped
3. City and ZIP code dropped

If filters were relaxed, the response includes a `cascade_message` field.

**Example request:**
```
curl "http://localhost:5000/api/search?last_name=SMITH&state=CA&year=2024"
```

**Example response:**
```json
{
  "results": [
    {
      "first_name": "JOHN",
      "last_name": "SMITH",
      "contribution_date": "2024-06-15",
      "recipient_name": "HARRIS FOR PRESIDENT",
      "amount": 250.0,
      "recipient_type": "P",
      "committee_id": "C00703975",
      "city": "LOS ANGELES",
      "state": "CA",
      "zip_code": "90210"
    }
  ],
  "total_results": 1842,
  "page": 1,
  "total_pages": 37
}
```

---

### 2. `GET /api/contributor` — Contributor Profile

Get all contributions and percentile data for a specific contributor.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `first_name` | string | Yes | | Contributor first name |
| `last_name` | string | Yes | | Contributor last name |
| `city` | string | No | | Filter by city |
| `state` | string | No | | Filter by state |
| `zip_code` | string | No | | Filter by ZIP (prefix match) |
| `sort_by` | string | No | `contribution_date` | `contribution_date` or `amount` |
| `order` | string | No | `desc` | `asc` or `desc` |
| `page` | int | No | 1 | Page number |

**Example request:**
```
curl "http://localhost:5000/api/contributor?first_name=JOHN&last_name=SMITH&zip_code=90210"
```

**Example response:**
```json
{
  "contributor": {
    "first_name": "JOHN",
    "last_name": "SMITH",
    "city": "",
    "state": "",
    "zip_code": "90210"
  },
  "contributions": [
    {
      "contribution_date": "2024-06-15",
      "recipient_name": "HARRIS FOR PRESIDENT",
      "amount": 250.0,
      "recipient_type": "P",
      "committee_id": "C00703975",
      "city": "LOS ANGELES",
      "state": "CA",
      "zip_code": "90210"
    }
  ],
  "total_amount": 3750.0,
  "percentiles": {
    "2024": {
      "percentile": 95.2,
      "rank": 12450,
      "total_amount": 3750.0,
      "contribution_count": 8,
      "total_donors": 259000
    },
    "2023": {
      "percentile": 92.1,
      "rank": 18200,
      "total_amount": 2100.0,
      "contribution_count": 5,
      "total_donors": 230500
    }
  },
  "page": 1,
  "total_pages": 1,
  "total_results": 8
}
```

**Notes:**
- Percentiles are only returned when `zip_code` is provided (needed for donor identification).
- Percentile keys are year strings. Higher percentile = more giving relative to other donors that year.
- Known conduit platforms (ActBlue, WinRed) are excluded from results.

---

### 3. `GET /api/recipient` — Recipient Details

Get top contributors to a specific committee/campaign.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `committee_id` | string | Yes | | FEC committee ID (e.g., `C00703975`) |
| `page` | int | No | 1 | Page number |

**Example request:**
```
curl "http://localhost:5000/api/recipient?committee_id=C00703975"
```

**Example response:**
```json
{
  "name": "HARRIS FOR PRESIDENT",
  "type": "P",
  "committee_id": "C00703975",
  "contributors": [
    {
      "first_name": "JANE",
      "last_name": "DOE",
      "total_amount": 6600.0
    },
    {
      "first_name": "JOHN",
      "last_name": "SMITH",
      "total_amount": 3750.0
    }
  ],
  "total_amount": 285000000.0,
  "page": 1,
  "total_pages": 500,
  "total_results": 24982
}
```

**Notes:**
- Contributors are ranked by total all-time contribution amount (descending).
- If the `committee_id` is a known conduit (ActBlue, WinRed), the response will include `"type": "passthrough"` with an explanatory message and empty contributors list.

---

### 4. `GET /api/search_recipients` — Search Recipients

Search for committees, campaigns, and organizations by name.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `q` | string | Yes | | Search query for recipient name |
| `sort_by` | string | No | `recent_activity` | `recent_activity`, `total_activity`, or `alphabetical` |
| `page` | int | No | 1 | Page number |

**Example request:**
```
curl "http://localhost:5000/api/search_recipients?q=democratic"
```

**Example response:**
```json
{
  "results": [
    {
      "committee_id": "C00010603",
      "name": "DEMOCRATIC CONGRESSIONAL CAMPAIGN CMTE",
      "type": "X",
      "total_contributions": 1250000,
      "total_amount": 185000000.0,
      "recent_contributions": 350000,
      "recent_amount": 52000000.0,
      "last_contribution_date": "2024-11-05"
    }
  ],
  "total_results": 245,
  "page": 1,
  "total_pages": 5
}
```

**Notes:**
- Uses Full-Text Search (FTS) when the recipient lookup table is available; falls back to LIKE search otherwise.
- `recent_contributions` and `recent_amount` cover the last 365 days.

---

### 5. `GET /api/person` — Person Search

Search for a person's contributions with cascading location logic. Defaults to CA state if no state specified.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `first_name` | string | Yes | | Person's first name |
| `last_name` | string | Yes | | Person's last name |
| `city` | string | No | | City filter |
| `state` | string | No | | Two-letter state code (searches all states if blank) |
| `zip_code` | string | No | | ZIP code filter |

**Example request:**
```
curl "http://localhost:5000/api/person?first_name=JOHN&last_name=SMITH&zip_code=90210"
```

**Example response:**
```json
{
  "person": {
    "first_name": "JOHN",
    "last_name": "SMITH",
    "city": "",
    "state": "",
    "zip_code": "90210"
  },
  "contributions": [
    {
      "contribution_date": "2024-06-15",
      "recipient_name": "HARRIS FOR PRESIDENT",
      "amount": 250.0,
      "committee_id": "C00703975",
      "city": "LOS ANGELES",
      "state": "CA",
      "zip_code": "90210"
    }
  ],
  "total_giving": 3750.0,
  "percentiles": {
    "2024": {
      "percentile": 95.2,
      "rank": 12450,
      "total_amount": 3750.0,
      "contribution_count": 8,
      "total_donors": 259000
    }
  }
}
```

**Notes:**
- Returns up to 10 most recent contributions.
- If no state is provided, searches all states.
- Cascading search: if initial filters yield no results, ZIP is dropped, then city and ZIP.
- If filters were relaxed, `cascade_message` field is included.

---

### 6. `GET /api/contributions_by_person` — Quick Person Lookup (Legacy)

Simple lookup of recent contributions for a person identified by name and ZIP.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `first_name` | string | Yes | | First name |
| `last_name` | string | Yes | | Last name |
| `zip_code` | string | Yes | | ZIP code (prefix match) |

**Example request:**
```
curl "http://localhost:5000/api/contributions_by_person?first_name=JOHN&last_name=SMITH&zip_code=90210"
```

**Example response:**
```json
[
  {
    "first_name": "JOHN",
    "last_name": "SMITH",
    "contribution_date": "2024-06-15",
    "recipient_name_resolved": "HARRIS FOR PRESIDENT",
    "amount": 250.0,
    "recipient_type_resolved": "P",
    "recipient_committee_id": "C00703975",
    "city": "LOS ANGELES",
    "state": "CA",
    "zip_code": "90210"
  }
]
```

**Notes:**
- Returns up to 20 most recent contributions.
- All three parameters are required.

---

## Data Types

### Contribution Object
| Field | Type | Description |
|-------|------|-------------|
| `first_name` | string | Contributor first name |
| `last_name` | string | Contributor last name |
| `contribution_date` | string | Date in `YYYY-MM-DD` format |
| `recipient_name` | string | Resolved recipient name |
| `amount` | float | Contribution amount in USD |
| `recipient_type` | string | FEC committee type code |
| `committee_id` | string | FEC committee ID |
| `city` | string | Contributor city |
| `state` | string | Contributor state (2-letter) |
| `zip_code` | string | Contributor ZIP code |

### Recipient Object
| Field | Type | Description |
|-------|------|-------------|
| `committee_id` | string | FEC committee ID |
| `name` | string | Committee/campaign name |
| `type` | string | Committee type code (H=House, S=Senate, P=Presidential, X=Party, etc.) |
| `total_contributions` | int | All-time contribution count |
| `total_amount` | float | All-time total amount received |
| `recent_contributions` | int | Contributions in last 365 days |
| `recent_amount` | float | Amount received in last 365 days |
| `last_contribution_date` | string | Date of most recent contribution |

### Percentile Object
| Field | Type | Description |
|-------|------|-------------|
| `percentile` | float | Percentile ranking (0-100, higher = more giving) |
| `rank` | int | Rank among all donors that year |
| `total_amount` | float | Total contributed that year |
| `contribution_count` | int | Number of contributions that year |
| `total_donors` | int | Total donors in the dataset that year |

---

## Conduit/Passthrough Filtering

The following platforms are automatically excluded from contribution searches as they are passthrough/conduit platforms:

| Committee ID | Name |
|-------------|------|
| `C00401224` | ActBlue |
| `C00694323` | WinRed |
| `C00708504` | NationBuilder |
| `C00580100` | Republican Platform Fund |

Contributions routed through these platforms are attributed to their ultimate recipient instead.
