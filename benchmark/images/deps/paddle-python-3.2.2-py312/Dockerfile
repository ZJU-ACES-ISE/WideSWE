FROM ecosyncbench/deps/pytorch-python:3.12

LABEL org.ecosyncbench.layer=deps \
      org.ecosyncbench.runtime=python \
      org.ecosyncbench.ecosystem=paddlepaddle \
      org.ecosyncbench.version=3.2.2-py312

RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir \
      paddlepaddle==3.2.2 \
      pytest \
      numpy \
      scipy \
      astor
