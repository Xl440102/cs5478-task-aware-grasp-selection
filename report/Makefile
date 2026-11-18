.PHONY: all assets clean
TECTONIC_FLAGS ?=

all:
	mkdir -p build
	@if command -v tectonic >/dev/null 2>&1; then \
		tectonic $(TECTONIC_FLAGS) --keep-logs --outdir build neurips_2026.tex; \
	elif command -v pdflatex >/dev/null 2>&1; then \
		pdflatex -halt-on-error -interaction=nonstopmode -output-directory=build neurips_2026.tex && \
		pdflatex -halt-on-error -interaction=nonstopmode -output-directory=build neurips_2026.tex; \
	else \
		echo "Install Tectonic or a TeX distribution with pdflatex."; exit 1; \
	fi
	cp build/neurips_2026.pdf final_report.pdf

assets:
	python3 prepare_results.py

clean:
	rm -rf build
