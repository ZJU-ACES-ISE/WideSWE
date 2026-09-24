Migrate to native lazy objects which were introduced with PHP 8.4. Instead of using ProxyManager, users on PHP 8.4 and newer can leverage native lazy objects. With var-exporter 7.3 or newer, the previous LazyGhostTrait approach produces deprecation warnings.

Add `setUseNativeLazyObject()` and `isNativeLazyObjectEnabled()` configuration support and use a `NativeLazyObjectFactory` when enabled. Native lazy objects and LazyGhostObject mode must not be enabled together. Preserve existing ODM behavior for lazy references and collections, mapped property access, lifecycle callbacks, and UnitOfWork operations.

Enable native lazy objects by default when using PHP 8.4 or newer and doctrine/mongodb-odm 2.14 or newer, and add an `enable_native_lazy_objects` option to disable this behavior. Make `AbstractManagerRegistry::$proxyInterfaceName` nullable because there is no proxy class wrapping the entity or document class when native lazy objects are used.
