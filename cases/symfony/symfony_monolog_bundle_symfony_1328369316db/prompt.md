Do not add a processor to all loggers if tags specify a channel or handler, and do not fall back to the default serializer if tags specify a named one.

When you add a custom processor to a specific channel or handler, it gets added to all channels because `ProcessorInterface` is registered for autoconfiguration. For example:

```php
#[AsMonologProcessor('my_channel')]
final class MyProcessor implements ProcessorInterface
{
    public function __invoke(LogRecord $record) {}
}
```

You would expect this processor to be registered only for `my_channel`, but due to autoconfiguration it is added to every channel.

When configuring services like normalizers for a named serializer or Monolog processors for specific channels or handlers, autoconfiguration can get in the way. If a custom normalizer is registered for a specific named serializer, it also gets registered with the default serializer because of autoconfiguration. Currently, the only way around this is to disable autoconfiguration entirely.

Explicit scoped tags should work without requiring autoconfiguration to be disabled. A named serializer tag should not also register the service with the default serializer, and a channel or handler tag should not also register a processor with all loggers. Existing behavior for services without an explicit scope, including non-scoping tag options, should remain unchanged. The same behavior applies to serializer normalizer and encoder tags.
