Fix Safari/Firefox browser keyboard input not working for TextFields in Flutter Web.

For the `SelectionArea` case:

- Run the given code with Flutter Web in the Safari or Firefox browser.
- Open the dialog.
- Try to write some text in each field.

The keyboard should work correctly. Half of the time the keyboard doesn't work. If you focus another field, sometimes it works and sometimes it doesn't. It works for the Chrome browser but not for Firefox or Safari on macOS.

For the `PointerInterceptor` case:

1. Add `PointerInterceptor` to your packages: `flutter pub add pointer_interceptor`.
2. Wrap a `TextField` widget with `PointerInterceptor`.
3. Run Flutter on Firefox or Safari.
4. Try to type something in the `TextField` widget.

You should be able to type. The `TextField` widget is completely unresponsive.
