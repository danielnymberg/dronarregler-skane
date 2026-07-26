SHELL := /bin/bash
PY := python3

.PHONY: help all build ingest docs extract verify data site test test-snabb \
        visuell servera uppdatera rensa-dist rensa-allt

help:
	@echo "Drönarkoll Skåne — pipeline"
	@echo ""
	@echo "  make all         hela kedjan: steg 1–6 + testsvit (grönt krävs för dist/)"
	@echo "  make ingest      steg 1  hämta områden ur NVR (WFS) + detaljposter"
	@echo "  make docs        steg 2  ladda ned beslutsdokument + text/OCR"
	@echo "  make extract     steg 3  extrahera föreskriftspunkter"
	@echo "  make verify      steg 4  verifiera varje citat mot källdokumentet"
	@echo "  make data        steg 5  bygg CC0-datapaketet i data/"
	@echo "  make site        steg 6  bygg dist/ (kräver grön testsvit via make all)"
	@echo "  make test        testsvit A–D"
	@echo "  make test-snabb  testsvit utan nätberoende länkhälsotest"
	@echo "  make visuell     visuell granskning i Chrome + skärmdumpar"
	@echo "  make servera     lokal server på http://localhost:8787"
	@echo "  make uppdatera   månadsuppdatering (diffar mot föregående manifest)"
	@echo "  make rensa-dist  ta bort dist/"
	@echo "  make rensa-allt  ta bort dist/, data/ och cache/ (hämtar om allt)"

# dist/ får bara skrivas när testsviten är grön. Därför byggs dist/ i två steg:
# först en kandidat, sedan tester, sedan behålls den. Faller testerna kvar står
# den gamla dist/ orörd via .dist-backup.
all: ingest docs extract verify data
	@$(MAKE) --no-print-directory _site-med-grind

_site-med-grind:
	@if [ -d dist ]; then rm -rf .dist-backup && cp -R dist .dist-backup; fi
	@$(PY) scripts/06_build_site.py
	@if $(PY) tests/test_suite.py --snabb; then \
	  echo ""; echo "Testsvit grön — dist/ behålls."; rm -rf .dist-backup; \
	else \
	  echo ""; echo "TESTSVIT RÖD — dist/ rullas tillbaka."; \
	  rm -rf dist; if [ -d .dist-backup ]; then mv .dist-backup dist; fi; exit 1; \
	fi

ingest:
	$(PY) scripts/01_ingest.py

docs:
	$(PY) scripts/02_fetch_docs.py

extract:
	$(PY) scripts/03_extract.py

verify:
	$(PY) scripts/04_verify.py

data:
	$(PY) scripts/05_build_data.py

site:
	$(PY) scripts/06_build_site.py

test:
	$(PY) tests/test_suite.py

test-snabb:
	$(PY) tests/test_suite.py --snabb

visuell:
	$(PY) scripts/07_visuell_granskning.py

servera:
	@echo "http://localhost:8787 — Ctrl+C avslutar"
	@cd dist && $(PY) -m http.server 8787

uppdatera:
	$(PY) scripts/uppdatera.py

rensa-dist:
	rm -rf dist .dist-backup

rensa-allt:
	rm -rf dist .dist-backup data cache
