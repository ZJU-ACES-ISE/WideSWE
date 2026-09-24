Add safe-class escaper support across Twig, TwigBundle, and TwigComponent.

Currently, `EnvironmentConfigurator` does not implement an interface. This makes it impossible to properly type-hint a decorator when a third-party bundle also decorates the same service.

Allow overriding `EscaperRuntime` via a custom runtime loader. Previously, `EscaperExtension::setEnvironment()` was calling `$environment->getRuntime(EscaperRuntime::class)` eagerly. Since this method is called from `Environment::__construct()`, the runtime was resolved before any custom runtime loader could be injected, making it impossible to override `EscaperRuntime`.

Register `Twig\Runtime\EscaperRuntime` as service `twig.runtime.escaper` so it can be customized via the service container. The `ContainerRuntimeLoader` takes priority over Twig's internal `defaultRuntimeLoader`, so lazy instantiation is preserved. The charset is injected at compile time.

Add a `twig.safe_class` resource tag to register safe classes for the escaper. A bundle can now mark a class as safe without decorating the environment configurator:

```php
->set(MyObject::class)
    ->resourceTag('twig.safe_class', ['strategy' => 'html'])
```

The `strategy` attribute should accept either a string or a list of strings, and multiple `twig.safe_class` tags on the same class should accumulate. A missing or invalid `strategy` should raise `InvalidArgumentException`. If the escaper runtime service is not defined, safe-class processing should do nothing.

In TwigComponent, use the `twig.safe_class` tag. When `symfony/twig-bundle` ships `SafeClassPass`, the `TwigEnvironmentConfigurator` decorator is no longer needed to register `ComponentAttributes` as a safe class. Detect its presence and use the new `twig.safe_class` resource tag instead, falling back to the decorator for older versions. Move the `setLexer()` call from the configurator into `TwigComponentPass`.
