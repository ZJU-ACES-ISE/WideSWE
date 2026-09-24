FROM ecosyncbench/deps/pytorch-python:3.12

RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir \
      paddlepaddle==3.2.2 \
      pytest \
      numpy \
      astor \
      black==22.8.0 \
      isort==5.11.5
