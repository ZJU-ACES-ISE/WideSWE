# CA1822 False positive/conflict with new Extension syntax

**Steps to Reproduce**:

```csharp
public static class MyExtensions
{
    extension (string s)
    {
        public int CountProp => s.Length; //Member 'CountProp' does not access instance data and can be marked as static
        public int CountMeth() => s.Length; //Member 'CountMeth' does not access instance data and can be marked as static
    }
}
```

**Expected Behavior**:

No CA1822 since this property/method is accessing the receiver as if it was an instance method or whatever the right word is in compiler speak

The `make member static` analyzer should distinguish extension members that access their receiver from extension members that do not: receiver-dependent members must not report CA1822, while members that do not access the receiver should remain eligible for the diagnostic and code fix. Support requires a Roslyn version that can parse extension members; keep existing analyzer and fixer behavior compatible with the Roslyn update, including the resulting meta-analyzer and formatting changes.

The Roslyn update must also preserve creation and compilation of skeleton cross-language project references, including a Visual Basic project referencing a C# project.
