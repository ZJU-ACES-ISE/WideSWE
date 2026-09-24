FROM ecosyncbench/deps/home-assistant-supervisor-py:2c6253e4b664

RUN /opt/ecosync/venv/bin/python -m pip install --no-cache-dir docker==7.1.0
