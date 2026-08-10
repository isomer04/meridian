# Meridian demo PDFs

These permanent, fictional samples exercise both document-intake paths:

- `meridian-demo-native-application.pdf` contains a searchable text layer.
- `meridian-demo-scanned-application.pdf` is image-only and exercises local Tesseract OCR.

Upload either file from **Run a loan -> Upload documents**. The scanned sample requires
Tesseract to be installed locally. All names, addresses, financial figures, and identifiers
are synthetic. The `900`-series SSN is deliberately non-issued test data.

Python checks whether an SSN-shaped value contains all nine digits and returns only the
`ssn_on_file` boolean with redacted provenance. The browser and frontier-model payloads
do not receive the SSN digits. The private local document store retains encrypted original PDFs
and selected proposed-field provenance excerpts, but not complete extracted page text. Real
customer documents still require the deployment's normal
encryption, retention, and access controls.

Regenerate both files from the repository root with:

```powershell
uv run python scripts/generate_demo_pdfs.py
```
