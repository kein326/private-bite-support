# Private Bite Support

Public support and privacy pages for the Private Bite iPhone app.

## Pages

- `index.html` — Japanese support page (FAQ, contact)
- `en/index.html` — English support page (FAQ, contact)
- `privacy/index.html` — Japanese privacy policy
- `en/privacy/index.html` — English privacy policy
- `import/index.html` — Japanese "Add records from a CSV file" guide
- `en/import/index.html` — English "Add records from a CSV file" guide

## Downloads

- `downloads/private-bite-import-v1-ja.csv` — official CSV import template (Japanese headers)
- `downloads/private-bite-import-v1-en.csv` — official CSV import template (English headers)

## Local verification

```powershell
python -m unittest discover -s tests -v
python -m http.server 8000
```

Open `http://localhost:8000/` and verify the Japanese and English pages.

The site intentionally uses no JavaScript, analytics, ads, cookies, external fonts, or CDN assets.
