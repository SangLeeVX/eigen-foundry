.PHONY: bootstrap test secrets contracts package check

bootstrap:
	python3 -m pip install --disable-pip-version-check -r requirements-ci.lock
	python3 -m pip install --disable-pip-version-check --no-deps -e .

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -t . -v

secrets:
	python3 forge/check_secrets.py

contracts:
	python3 forge/validate_contracts.py

package:
	PIP_NO_INDEX=1 python3 -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist

check: secrets contracts test package
