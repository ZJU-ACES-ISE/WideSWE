Add calendar delegation.

As a Nextcloud Calendar user, I want to delegate my calendar(s) to another Nextcloud user, so that they can act on my behalf. This would be used by assistants, vacation replacements, and similar. For example, Christoph shares a calendar with Jos, and Jos creates a meeting with Christoph as the organizer.

Leverage the existing backend support with additional UI, and make sure that all operations the calendar owner can do can also be done by the delegatee. Include the DAV delegation service needed by the Calendar app, using calendar proxy read/write groups, `group-member-set`, `group-membership`, `calendar-home-set`, read/write delegate permissions, and delegated calendar homes. Expose the client helpers `getCalendarHomeForUrl`, `getGroupMemberSet`, `setGroupMemberSet`, `getGroupMembership`, `getCalendarHomeUrlForPrincipal`, `getDelegatesForPrincipal`, `addDelegate`, `removeDelegate`, `getDelegatorPrincipalUrls`, and `getDelegatorsWithPermission`; calendar-home lookup should reuse a known home, support an arbitrary principal, and return no URL when `calendar-home-set` is absent.

Delegated calendars should be visible and manageable in Calendar settings and navigation. Users should be able to view, add, and revoke delegates, choose read or write delegation, load calendars delegated to them, and distinguish delegated calendars from ordinary shared calendars with delegation metadata such as `isDelegated` and `delegatorUrl`.

Delegation must also work when a calendar was shared with the delegator: if user 1 shares a calendar with user 2 and user 2 delegates to user 3, user 3 should be able to access that shared calendar with the same read or write access that user 2 has.
