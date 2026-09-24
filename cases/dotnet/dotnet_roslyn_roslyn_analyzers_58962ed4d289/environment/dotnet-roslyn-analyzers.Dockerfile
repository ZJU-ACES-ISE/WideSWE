FROM ecosyncbench/deps/dotnet-roslyn-sdk-redirector:84722594faef

LABEL org.ecosyncbench.layer=case-runtime \
      org.ecosyncbench.ecosystem=dotnet \
      org.ecosyncbench.runtime=dotnet-roslyn-analyzers-extension-members

WORKDIR /workspace
