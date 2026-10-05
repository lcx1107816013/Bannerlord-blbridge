// Shims for the offline unit tests, needed ONLY by the ledger-gap assertions (2026-10-05).
//
// SCOPE -- read before trusting results
// ------------------------------------
// Two things are stubbed and ONLY these two:
//
//   1) `TaleWorlds.Library.Debug.Print(string)` -- the engine RGL outlet used by
//      `ActionLedger.ExceptionToRgl`. Not the subject of any assertion; the whole call
//      site is inside try/catch anyway. This stub RECORDS what was printed so a test can
//      assert "the failure was reported to the second outlet" if desired.
//
//   2) `CommandPump` -- but only its path helpers `CommandsRoot` / `EnsureDirs`, whose real
//      bodies are trivial (`Path.Combine(BridgeConfig.LogDir,"commands")` / three
//      `Directory.CreateDirectory` calls). The real `CommandPump.cs` cannot be compiled here
//      because its `Dispatch` reaches into TaleWorlds-dependent probes.
//
// EVERYTHING ACTUALLY ASSERTED IS SHIPPED CODE: `ActionLedger.Record` (including its new
// non-silent catch), `Protocol.ReadResponseOutcome`, `Jmini`, `BridgeConfig`.

using System;
using System.Collections.Generic;
using System.IO;

namespace TaleWorlds.Library
{
    /// <summary>Stand-in for the engine debug outlet; records calls so tests can observe them.</summary>
    internal static class Debug
    {
        internal static readonly List<string> Printed = new List<string>();

        public static void Print(string text)
        {
            Printed.Add(text ?? "");
        }
    }
}

namespace BlBridge
{
    /// <summary>Path-helper stand-in for the real CommandPump (see scope note above).</summary>
    internal static class CommandPump
    {
        public static string CommandsRoot
        {
            get { return Path.Combine(BridgeConfig.LogDir, "commands"); }
        }

        public static string PendingDir { get { return Path.Combine(CommandsRoot, "pending"); } }
        public static string DoneDir { get { return Path.Combine(CommandsRoot, "done"); } }

        public static void EnsureDirs()
        {
            try
            {
                if (!Directory.Exists(CommandsRoot)) Directory.CreateDirectory(CommandsRoot);
                if (!Directory.Exists(PendingDir)) Directory.CreateDirectory(PendingDir);
                if (!Directory.Exists(DoneDir)) Directory.CreateDirectory(DoneDir);
            }
            catch
            {
            }
        }
    }
}
