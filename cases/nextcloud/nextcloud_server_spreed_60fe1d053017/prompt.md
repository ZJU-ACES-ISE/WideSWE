Mentions inside markdown code blocks should not be rendered and should not trigger notifications.

## Steps to reproduce

1. Post a message like `@ivan`.
2. Post a message like `Hello @ivan world`.
3. Post messages where `@ivan` appears inside inline markdown code or a fenced code block.

## Expected behaviour

Mentions inside markdown code should be silent: no notification should be sent and no mention chip should be shown. Mentions outside markdown code should continue to work normally.

## Actual behaviour

The mentioned user receives notifications and the mention chip is shown for occurrences inside markdown code.

By default, `getMentions(bool $supportMarkdown = true)` should ignore mentions inside valid inline code and backtick- or tilde-fenced code blocks. Callers that pass `false` should retain the previous non-markdown-aware behaviour, and unmatched code delimiters should not hide otherwise valid mentions.
