FROM ecosyncbench/deps/pytorch-python:3.12

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m pip install --no-cache-dir \
    "pytest>=7.2,<9" \
    pytest-xdist \
    pytest-random-order \
    pytest-rerunfailures \
    pytest-order \
    pytest-env \
    pytest-rich \
    pytest-timeout \
    parameterized \
    psutil \
    numpy \
    packaging \
    pyyaml \
    regex \
    requests \
    tqdm \
    filelock \
    huggingface_hub \
    "tokenizers>=0.22.0,<=0.23.0" \
    "safetensors>=0.4.3" \
    accelerate \
    protobuf \
    sentencepiece \
    jinja2 \
    datasets \
    evaluate \
    Pillow \
    scipy \
    scikit-learn
