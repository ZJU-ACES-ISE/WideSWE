Add a `-b, --browser` option to CLI login for browser-based authentication, and keep Expo CLI and EAS CLI behavior in parity. Include the option in login help.

Support for third-party sign in (Apple/Google/GitHub). Use the browser flow for both regular browser login and SSO, preserving whether SSO was requested and saving the authenticated session.

Change the local web server to redirect to expo.dev on success and failures so that the webpage looks better and more professional.
