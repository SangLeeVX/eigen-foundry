.PHONY: bootstrap test secrets contracts schemas package reproducible check

bootstrap:
	python3 -m pip install --disable-pip-version-check --require-hashes -r requirements-ci.lock
	PIP_NO_INDEX=1 python3 -m pip install --disable-pip-version-check --no-deps --no-build-isolation -e .

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -t . -v

secrets:
	python3 forge/check_secrets.py

contracts:
	python3 forge/validate_contracts.py

schemas:
	PYTHONPATH=src python3 scripts/export_schemas.py
	git diff --exit-code -- schemas/

package:
	PIP_NO_INDEX=1 python3 -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist

reproducible:
	PIP_NO_INDEX=1 python3 scripts/check_reproducible_wheel.py

check: secrets contracts test schemas reproducible
