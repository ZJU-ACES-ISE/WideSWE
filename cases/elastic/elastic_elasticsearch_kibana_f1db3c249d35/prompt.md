Support subqueries in `WHERE`.

Enable `IN` and `NOT IN` non-correlated subqueries behind snapshot.

The feature applies to the `WHERE` command as a predicate/filter and is supported under `FROM` context only.

**Scope**

- Non-Correlated Subqueries Only: The subquery must be independent and cannot reference fields from the outer query block.
- Primitive Type Support: The feature applies to all primitive data types currently supported by ES|QL (e.g., keyword, long, ip, date).
- Predicate Support: Both IN and NOT IN operators are supported within the WHERE command and FROM context.
- Conjunction and Disjunction: The IN and NOT IN subqueries are predicates, so conjunction and disjunction should be supported.
- Nested IN Subqueries: Nested `IN` and `NOT IN` subqueries are supported.
- Single-Column Output: The subquery returns exactly one output field, and its type must be compatible with the value on the left side of the predicate.

The subquery form is supported only as a direct `WHERE` predicate. It is not supported in other commands such as `EVAL`, `SORT`, `STATS BY`, or as an argument nested inside another expression.

IN subquery that references views is not supported in this part.

The ES|QL editor should support these subqueries in parsing, autocomplete, and validation.

Add ES|QL autocomplete for `WHERE ... IN` subqueries. The feature is hidden by default.

Support nested queries and only make it available for the `FROM` source command.
