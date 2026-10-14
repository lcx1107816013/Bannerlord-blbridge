using System;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// Null checks for TaleWorlds engine types.
    ///
    /// ─────────────────────────────────────────────────────────────────────────
    /// THE TRAP THIS EXISTS FOR
    ///
    /// TaleWorlds engine types (Scene, Mission, MetaMesh, SceneView, GameEntity,
    /// ...) derive from `TaleWorlds.DotNet.NativeObject`, which OVERLOADS `==` and
    /// `!=` to compare native pointers. Those operators run through the type's static
    /// constructor, and that constructor needs the engine's native function table.
    ///
    /// Consequence: writing the natural
    ///
    ///     if (scene != null) { ... }
    ///
    /// does NOT do a reference comparison. It calls NativeObject.op_Inequality, whose
    /// static initializer throws `TypeInitializationException` (with an inner
    /// NullReferenceException) whenever the game is not hosting the code — for
    /// example in the offline test harness, or in any diagnostic path that runs
    /// before the engine is up.
    ///
    /// That is exactly backwards from what a null check is for: the check is supposed
    /// to be the thing that makes a "no game here" path SAFE, and instead it is the
    /// thing that throws. It was found by the offline suite reporting
    /// `status 不抛异常 [FAIL]` with this stack:
    ///
    ///     TaleWorlds.DotNet.NativeObject..cctor()
    ///     TaleWorlds.DotNet.NativeObject.op_Inequality(NativeObject, NativeObject)
    ///     BlBridge.EditorProbe.EditorProbeMainThread.Status()
    ///
    /// `ReferenceEquals` is a static method on System.Object, so it cannot bind to a
    /// type's overloaded operator and performs a genuine reference comparison. That
    /// is why every engine-type null check in this module goes through here.
    ///
    /// NOTE: `x ?? y` is SAFE, because the null-coalescing operator emits a direct
    /// IL null check rather than an overloaded operator call. Only `==` / `!=` are
    /// affected.
    /// </summary>
    internal static class EngineNull
    {
        /// <summary>True when <paramref name="value"/> is a null reference.</summary>
        internal static bool IsNull(object value)
        {
            return ReferenceEquals(value, null);
        }

        /// <summary>True when <paramref name="value"/> is not a null reference.</summary>
        internal static bool IsNotNull(object value)
        {
            return !ReferenceEquals(value, null);
        }
    }
}
