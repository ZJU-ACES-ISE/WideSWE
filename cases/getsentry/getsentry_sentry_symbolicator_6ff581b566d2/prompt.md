Support complete Java frame symbolication when ProGuard `rewriteFrame` annotations are involved.

Store rewrite rules in `ProguardCache` format, decode them, and use them for symbolication. Additionally parse the exception descriptor.

Support the ProGuard `rewriteFrame` annotation. Extend the `JvmStacktrace` interface, because the `JvmException` associated with the stacktrace is needed to correctly apply rewrite rules.

Pass the exception to the Java symbolication request. Preserve its type and optional module for exception stacktraces; `get_exception()` should return the module-qualified type when a module is present and only the type otherwise. Top-level and thread stacktraces are not exceptions. This is necessary for complete symbolication of Java frames when there are `rewriteFrame` annotations involved.
