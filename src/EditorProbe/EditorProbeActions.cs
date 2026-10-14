using System;
using System.Collections.Generic;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// The action vocabulary, mirrored from the archive client's
    /// EDITOR_PROBE_ACTIONS. The client refuses to SEND anything outside this list,
    /// and this probe refuses to ACCEPT anything outside it. Both sides check, so a
    /// version skew shows up as an explicit `invalid_request` rather than a hang.
    ///
    /// Each action also declares whether it needs the scene to be open in the
    /// editor. The client already gates most of them behind `requireActiveScene`,
    /// but the probe re-checks: the client's check is a courtesy, the probe's is the
    /// one that protects the engine.
    /// </summary>
    internal static class EditorProbeActions
    {
        internal const string Status = "status";
        internal const string EnterEditor = "enter_editor";
        internal const string OpenScene = "open_scene";
        internal const string Entities = "entities";
        internal const string PlaceProbe = "place_probe";
        internal const string MoveProbe = "move_probe";
        internal const string SaveScene = "save_scene";
        internal const string CloseScene = "close_scene";
        internal const string Quit = "quit";
        internal const string Capture = "capture";
        internal const string ApplyLayout = "apply_layout";
        internal const string LayoutEntities = "layout_entities";
        internal const string Focus = "focus";
        internal const string Camera = "camera";
        internal const string PrefabInfo = "prefab_info";

        private static readonly string[] All =
        {
            Status, EnterEditor, OpenScene, Entities, PlaceProbe, MoveProbe, SaveScene,
            CloseScene, Quit, Capture, ApplyLayout, LayoutEntities, Focus, Camera, PrefabInfo,
        };

        /// <summary>Actions the client runs behind requireActiveScene.</summary>
        private static readonly HashSet<string> NeedsActiveScene = new HashSet<string>(StringComparer.Ordinal)
        {
            Entities, PlaceProbe, MoveProbe, SaveScene, Capture, ApplyLayout, LayoutEntities,
            Focus, Camera, PrefabInfo, CloseScene,
        };

        internal static bool IsKnown(string action)
        {
            for (int i = 0; i < All.Length; i++)
            {
                if (string.Equals(All[i], action, StringComparison.Ordinal)) return true;
            }
            return false;
        }

        internal static bool RequiresActiveScene(string action)
        {
            return NeedsActiveScene.Contains(action);
        }

        internal static string[] AllActions()
        {
            string[] copy = new string[All.Length];
            Array.Copy(All, copy, All.Length);
            return copy;
        }
    }
}
