Monthly AAP cost does not match cost of automated execution.

Subscription cost was not being calculated correctly. The relevant `SubscriptionCost` APIs are `cost_per_elapsed_second` and `per_second_subscription_cost`, depending on the service.

The monthly subscription cost should be distributed proportionally across all job elapsed seconds in a period, ensuring the total distributed cost matches the period's subscription cost. Report job cost calculations should use this elapsed-time-based subscription cost.

Date ranges are inclusive calendar-day ranges. If either boundary is omitted, use the current calendar month; if the start is later than the end, treat the boundaries in chronological order. Full months, partial months, leap years, multi-month spans, and cross-year spans should prorate each month's subscription cost by the number of included days.

Only successful and failed jobs whose finish time is within the period contribute to total elapsed time; pending, running, and cancelled jobs do not. Return the elapsed-second rate as a `Decimal` quantized to `0.0000000001`. If no eligible elapsed time exists, `cost_per_elapsed_second` retains its `Decimal('0.000001')` fallback, while `per_second_subscription_cost` returns quantized zero.
