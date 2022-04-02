venv: requirements.txt
	pyenv virtualenv -f 3.10.4 spotify-matcher
	$(shell pyenv virtualenv-prefix spotify-matcher)/bin/pip install -U pip wheel
	$(shell pyenv virtualenv-prefix spotify-matcher)/bin/pip install -U pip-tools
	$(shell pyenv virtualenv-prefix spotify-matcher)/bin/pip-sync requirements.txt

requirements.txt: requirements.in
	$(shell pyenv virtualenv-prefix spotify-matcher)/bin/pip-compile requirements.in

.PHONY: venv
