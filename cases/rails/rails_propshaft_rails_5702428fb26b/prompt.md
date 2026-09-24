CSS and HTML assets should be served with an explicit UTF-8 charset.

CSS files served with `Content-Type: text/css` and no `charset=utf-8` can be decoded incorrectly in Chrome. When Chrome discovers a stylesheet via a `<link>` tag during HTML parsing, it may inherit the document's encoding or fall back to Windows-1252 instead of defaulting to UTF-8. This causes non-ASCII characters in CSS, such as an en-dash U+2013 in `content` properties, to render as mojibake.

The same issue affects HTML assets, including static HTML files in `public/` that lack a `<meta charset>` tag. The CSS spec defaults to UTF-8, but Chrome does not reliably follow this default. Adding `charset=utf-8` to the `Content-Type` header is the highest-priority encoding signal in the spec's detection chain.

Only CSS and HTML MIME types should get this charset behavior. `text/javascript` is excluded per RFC 9239, `text/xml` could break encoding declarations, and `text/plain` has no demonstrated browser bug.

Propshaft assets should expose the resulting MIME value through `content_type_with_charset`: CSS and HTML return their content type with `; charset=utf-8`, while other MIME types retain their original content type.
