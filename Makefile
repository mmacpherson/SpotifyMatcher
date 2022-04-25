venv: requirements.txt
	pyenv virtualenv -f 3.10.4 spotify-matcher
	$(shell pyenv prefix spotify-matcher)/bin/pip install -U pip wheel
	$(shell pyenv prefix spotify-matcher)/bin/pip install -U pip-tools
	$(shell pyenv prefix spotify-matcher)/bin/pip-sync requirements.txt
	$(shell pyenv prefix spotify-matcher)/bin/python -m ipykernel install --user --name spotify-matcher --display-name "Py3 (spotify-matcher)"

requirements.txt: requirements.in
	$(shell pyenv prefix spotify-matcher)/bin/pip-compile requirements.in

.PHONY: venv
