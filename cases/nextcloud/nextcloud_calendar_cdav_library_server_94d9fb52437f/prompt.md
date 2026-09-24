Add the possibility to configure a default reminder for all-day events.

The calendar can set a default reminder that is used for all meetings. A reminder such as 10 minutes before the event is fine for regular events, but for all-day events it means the reminder is shown by default at 11:50pm before the day starts. For all-day events, users may prefer something different, for example 15 hours before, at 9:00am the day before.

Enhance the setting to define a default reminder for normal appointments and for all-day events. The calendar data exposed through DAV/CalDAV and the client model should support separate part-day and full-day default alarm values, including `default-alarm-part-day`, `default-alarm-full-day`, `defaultAlarmPartDay`, and `defaultAlarmFullDay`. User settings should expose `defaultReminderPartDay` and `defaultReminderFullDay`; accept `none` or integer-second offsets, reject invalid values, and allow either calendar alarm property to be cleared.

Loading, saving, and creating calendar objects should use the part-day default for timed events and the full-day default for all-day events, while keeping existing calendar behavior compatible.
