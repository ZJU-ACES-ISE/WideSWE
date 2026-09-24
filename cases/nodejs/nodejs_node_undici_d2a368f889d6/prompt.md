Unify HTTP header treatment.

HTTP header values are byte strings and should be turned into strings using isomorphic decoding, effectively latin1, not UTF-8.

In Node.js, header serialization is treated differently when the `Transfer-Encoding: chunked` header exists or not. Having two different behaviours for serializing the header causes problems when users do not know how it works. The behaviour should be unified by converting header values to latin1 in all cases.

In the old interceptor API, `onHeaders()` receives raw headers as an array where each value is a `Buffer`. In the new interceptor API, `onResponseStart()` receives headers as an object where each value is a string. However, the new API appears to decode these headers as UTF-8. The `NEW API` should give the same results as the `OLD API`.

Non-UTF-8 `content-disposition` values should be handled consistently with and without chunked transfer encoding. `parseHeaders` and `parseRawHeaders` should decode single, duplicate, and array header values using latin1/isomorphic decoding.
