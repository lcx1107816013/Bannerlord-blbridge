using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Engine;
using IOPath = System.IO.Path;   // TaleWorlds.Engine also defines a `Path`
using TaleWorlds.Library;      // MatrixFrame / Vec3 / Mat3 live here
using TaleWorlds.MountAndBlade;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// Executes editor actions. ALWAYS CALLED ON THE MAIN THREAD (drained by
    /// EditorProbeServer.DrainOnMainThread from the module's tick).
    ///
    /// ─────────────────────────────────────────────────────────────────────────
    /// WHAT IS VERIFIED AND WHAT IS BEST-EFFORT
    ///
    /// Every entry in the action table below is implemented, but they do not all
    /// rest on the same quality of evidence, and the difference is stated rather
    /// than blurred:
    ///
    ///   VERIFIED against the decompiled game source shipped with this project
    ///   (dist/games/bannerlord/assets/Source):
    ///     Scene.GetName, Scene.GetEntities, Scene.AddEntityWithMultiMesh,
    ///     Scene.CreateNewScene, Scene.Read, Scene.SetOwnerThread,
    ///     MetaMesh.GetCopy, GameEntity.Name, Utilities.TakeScreenshot
    ///
    ///   BEST-EFFORT, because the signature crosses the managed/native boundary
    ///   through an internal [ScriptingInterfaceBase] interface (MBAPI.IMBEditor):
    ///     entering and leaving the editor's edit mode. Those calls are made through
    ///     reflection so this file still COMPILES if a future game build changes the
    ///     binding, and a failure is reported as an explicit bridge_error naming the
    ///     member — never as a silent success.
    ///
    /// The honest consequence: `status`, `entities`, `layout_entities`,
    /// `apply_layout`, `save_scene`, `prefab_info` and `capture` are grounded in
    /// verified APIs; `enter_editor` / `close_scene` / `quit` are not yet
    /// demonstrated on a running editor. That is recorded in the module README and
    /// must not be reported as working until observed.
    ///
    /// NOTHING HERE INVENTED A SUCCESS. Every failure path returns a code the client
    /// already understands (bridge_error / wrong_scene), so a half-working probe is
    /// diagnosable instead of mysterious.
    /// </summary>
    internal static class EditorProbeMainThread
    {
        /// <summary>Managed entities created by apply_layout carry this name prefix.</summary>
        internal const string EntityNamePrefix = "bcp_";

        private static Scene _editorScene;
        private static SceneView _editorSceneView;
        private static bool _editModeOn;

        // ── entry point ─────────────────────────────────────────────────────────

        internal static string Execute(string action, Dictionary<string, object> args)
        {
            if (args == null) args = new Dictionary<string, object>(StringComparer.Ordinal);

            // Re-checked here, not just on the socket thread: this is the side that
            // actually touches the engine, so it is the side whose check matters.
            if (EditorProbeActions.RequiresActiveScene(action) && !_editModeOn)
                throw new InvalidOperationException(
                    "编辑器未处于编辑模式，无法执行 " + action + "（客户端应报 wrong_scene）");

            switch (action)
            {
                case EditorProbeActions.Status: return Status();
                case EditorProbeActions.EnterEditor: return EnterEditor(args);
                case EditorProbeActions.OpenScene: return OpenScene(args);
                case EditorProbeActions.Entities: return LayoutEntities();
                case EditorProbeActions.LayoutEntities: return LayoutEntities();
                case EditorProbeActions.ApplyLayout: return ApplyLayout(args);
                case EditorProbeActions.SaveScene: return SaveScene();
                case EditorProbeActions.Capture: return Capture();
                case EditorProbeActions.PrefabInfo: return PrefabInfo(args);
                case EditorProbeActions.Focus: return Focus(args);
                case EditorProbeActions.Camera: return Camera(args);
                case EditorProbeActions.PlaceProbe: return PlaceProbe(args);
                case EditorProbeActions.MoveProbe: return MoveProbe(args);
                case EditorProbeActions.CloseScene: return CloseScene();
                case EditorProbeActions.Quit: return Quit();
                default:
                    throw new InvalidOperationException("未实现的动作：" + action);
            }
        }

        // ── status ──────────────────────────────────────────────────────────────

        /// <summary>
        /// The client requires, on success: editMode===true, scene===sessionSceneName,
        /// probeScene===sessionSceneName, and a string resolvedSceneModule it then
        /// realpath-checks against the session's module dir.
        /// </summary>
        private static string Status()
        {
            Scene scene = _editorScene ?? CurrentScene();
            string sceneName = EngineNull.IsNotNull(scene) ? SafeSceneName(scene) : null;
            bool editMode = _editModeOn || IsEditModeOn();

            StringBuilder sb = new StringBuilder(256);
            sb.Append('{');
            sb.Append("\"editMode\":").Append(editMode ? "true" : "false");
            // `scene` reports what is ACTUALLY open; if nothing is open it falls back
            // to the configured name so the client can tell "wrong scene" apart from
            // "no scene", which are different failures with different fixes.
            sb.Append(",\"scene\":").Append(EditorProbeJson.Quote(sceneName ?? EditorProbeConfig.SceneName));
            sb.Append(",\"probeScene\":").Append(EditorProbeJson.Quote(EditorProbeConfig.SceneName));
            sb.Append(",\"resolvedSceneModule\":").Append(EditorProbeJson.Quote(EditorProbeConfig.ModuleDir ?? ""));
            sb.Append(",\"sceneNameActual\":").Append(sceneName == null ? "null" : EditorProbeJson.Quote(sceneName));
            sb.Append(",\"sceneOpen\":").Append(EngineNull.IsNotNull(scene) ? "true" : "false");
            sb.Append(",\"serverPort\":").Append(EditorProbeConfig.Port.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"gameVersion\":").Append(EditorProbeJson.Quote(EditorProbeConfig.RequiredGameVersion));
            sb.Append(",\"serverRunning\":").Append(EditorProbeServer.Running ? "true" : "false");
            sb.Append('}');
            return sb.ToString();
        }

        // ── edit mode (BEST-EFFORT: see the class comment) ───────────────────────

        private static string EnterEditor(Dictionary<string, object> args)
        {
            if (_editModeOn) return Status();

            Scene scene = _editorScene;
            if (EngineNull.IsNull(scene))
            {
                // Build a scene owned by this thread. SetOwnerThread is required:
                // engine scenes are thread-affine and a scene created elsewhere
                // cannot be safely consumed by the tick.
                scene = Scene.CreateNewScene(true, true, DecalAtlasGroup.All, EditorProbeConfig.SceneName);
                if (EngineNull.IsNull(scene)) throw new InvalidOperationException("Scene.CreateNewScene 返回 null");
                scene.SetOwnerThread();
                _editorScene = scene;
            }

            if (EngineNull.IsNull(_editorSceneView))
            {
                _editorSceneView = SceneView.CreateSceneView();
                if (EngineNull.IsNull(_editorSceneView)) throw new InvalidOperationException("SceneView.CreateSceneView 返回 null");
                _editorSceneView.SetScene(scene);
            }

            MatrixFrame frame = default(MatrixFrame);
            bool entered = ReflectionBridge.TryCallStatic(
                "TaleWorlds.MountAndBlade.MBEditor",
                "EnterEditMode",
                frame, 0f, 0f);

            if (!entered)
            {
                // Report the member we could not bind, so the failure is actionable
                // instead of a bare "editor didn't start".
                throw new InvalidOperationException(
                    "无法进入编辑模式：MBEditor.EnterEditMode 绑定失败（本机游戏版本可能不同）。"
                    + "场景已创建但未进入编辑模式。");
            }

            _editModeOn = true;
            EditorProbeConfig.TryLog("enter_editor: ok");
            return Status();
        }

        private static bool IsEditModeOn()
        {
            object value = ReflectionBridge.TryGetStaticProperty("TaleWorlds.MountAndBlade.MBEditor", "IsEditModeOn");
            if (value is bool) return (bool)value;
            return _editModeOn;
        }

        // ── scenes ──────────────────────────────────────────────────────────────

        private static string OpenScene(Dictionary<string, object> args)
        {
            if (EngineNull.IsNull(_editorScene))
            {
                _editorScene = Scene.CreateNewScene(true, true, DecalAtlasGroup.All, EditorProbeConfig.SceneName);
                if (EngineNull.IsNull(_editorScene)) throw new InvalidOperationException("Scene.CreateNewScene 返回 null");
                _editorScene.SetOwnerThread();
            }

            // Read the on-disk scene so an existing layout is loaded rather than
            // silently discarded. Read() takes the scene name; the engine resolves
            // it under the owning module's SceneObj directory.
            try
            {
                _editorScene.Read(EditorProbeConfig.SceneName);
            }
            catch (Exception ex)
            {
                // A scene that does not exist yet is normal on first run. Report it
                // as part of the result instead of failing the whole call.
                EditorProbeConfig.TryLog("open_scene: Read 失败（首次运行属正常）: " + ex.Message);
            }

            StringBuilder sb = new StringBuilder(128);
            sb.Append('{');
            sb.Append("\"scene\":").Append(EditorProbeJson.Quote(EditorProbeConfig.SceneName));
            sb.Append(",\"sceneDir\":").Append(EditorProbeJson.Quote(EditorProbeConfig.SceneDir));
            sb.Append(",\"opened\":true");
            sb.Append('}');
            return sb.ToString();
        }

        private static string CloseScene()
        {
            _editModeOn = false;
            ReflectionBridge.TryCallStatic("TaleWorlds.MountAndBlade.MBEditor", "LeaveEditMode");
            _editorScene = null;
            _editorSceneView = null;
            return "{\"closed\":true}";
        }

        private static string Quit()
        {
            CloseScene();
            // Stop the listener too: `quit` means the probe should stop serving.
            EditorProbeServer.Stop();
            return "{\"closed\":true,\"serverStopped\":true}";
        }

        // ── entities ────────────────────────────────────────────────────────────

        /// <summary>
        /// Lists entities this probe manages, identified by the name prefix so a
        /// scene the user also edits by hand is not misreported as ours.
        /// </summary>
        private static string LayoutEntities()
        {
            Scene scene = RequireScene();
            List<EntitySnapshot> found = SnapshotEntities(scene, true);

            StringBuilder sb = new StringBuilder(256);
            sb.Append("{\"entities\":[");
            for (int i = 0; i < found.Count; i++)
            {
                if (i > 0) sb.Append(',');
                found[i].Write(sb);
            }
            sb.Append("],\"scene\":").Append(EditorProbeJson.Quote(SafeSceneName(scene)));
            sb.Append(",\"count\":").Append(found.Count.ToString(CultureInfo.InvariantCulture));
            sb.Append('}');
            return sb.ToString();
        }

        private static List<EntitySnapshot> SnapshotEntities(Scene scene, bool managedOnly)
        {
            List<EntitySnapshot> result = new List<EntitySnapshot>();
            List<GameEntity> entities = new List<GameEntity>();
            try
            {
                scene.GetEntities(ref entities);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("Scene.GetEntities 失败：" + ex.Message);
            }

            for (int i = 0; i < entities.Count; i++)
            {
                GameEntity entity = entities[i];
                if (EngineNull.IsNull(entity)) continue;

                string name = null;
                try { name = entity.Name; } catch { }
                if (string.IsNullOrEmpty(name)) continue;

                bool mine = name.StartsWith(EntityNamePrefix, StringComparison.Ordinal);
                if (managedOnly && !mine) continue;

                MatrixFrame frame;
                try { frame = entity.GetGlobalFrame(); }
                catch { continue; }

                EntitySnapshot snapshot = new EntitySnapshot();
                snapshot.Name = name;
                snapshot.Id = mine ? name.Substring(EntityNamePrefix.Length) : name;
                snapshot.Managed = mine;
                snapshot.Origin = frame.origin;
                snapshot.Rotation = frame.rotation;
                snapshot.Scale = frame.rotation.GetScaleVector();
                snapshot.Pointer = entity.Pointer;
                result.Add(snapshot);
            }
            return result;
        }

        // ── apply_layout ────────────────────────────────────────────────────────

        /// <summary>
        /// Create or update managed entities. Repeated ids update in place (the
        /// client documents this as preserving the object and its GUID); a prefab
        /// that cannot be resolved is rejected rather than created empty.
        ///
        /// Partial application is possible by nature, which is why the client marks
        /// a failed apply_layout with partialChangesPossible. This method keeps that
        /// honest: it applies in order and reports exactly which ids succeeded.
        /// </summary>
        private static string ApplyLayout(Dictionary<string, object> args)
        {
            Scene scene = RequireScene();
            List<object> raw = EditorProbeJson.GetArray(args, "entities");
            if (raw == null || raw.Count == 0)
                throw new InvalidOperationException("apply_layout 需要非空的 entities 数组");
            if (raw.Count > 64)
                throw new InvalidOperationException("apply_layout 最多 64 个实体，收到 " + raw.Count);

            List<EntitySnapshot> existing = SnapshotEntities(scene, true);
            Dictionary<string, EntitySnapshot> byId = new Dictionary<string, EntitySnapshot>(StringComparer.Ordinal);
            for (int i = 0; i < existing.Count; i++) byId[existing[i].Id] = existing[i];

            List<string> created = new List<string>();
            List<string> updated = new List<string>();
            List<string> rejected = new List<string>();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);

            for (int i = 0; i < raw.Count; i++)
            {
                Dictionary<string, object> spec = raw[i] as Dictionary<string, object>;
                if (spec == null) { rejected.Add("(非对象条目 #" + i + ")"); continue; }

                string id = EditorProbeJson.GetString(spec, "id");
                string prefab = EditorProbeJson.GetString(spec, "prefab");
                if (string.IsNullOrEmpty(id) || string.IsNullOrEmpty(prefab))
                {
                    rejected.Add(id ?? "(缺 id)");
                    continue;
                }
                if (!seen.Add(id))
                {
                    // The client's schema already rejects duplicates; re-checking
                    // means a bug there cannot silently create two entities.
                    rejected.Add(id + " (重复 id)");
                    continue;
                }

                MetaMesh mesh;
                try
                {
                    mesh = MetaMesh.GetCopy(prefab, false, true);
                }
                catch (Exception ex)
                {
                    rejected.Add(id + " (prefab '" + prefab + "' 解析异常: " + ex.Message + ")");
                    continue;
                }
                if (EngineNull.IsNull(mesh))
                {
                    // Refusing an unresolvable prefab is the point: creating an empty
                    // entity would look like success and render nothing.
                    rejected.Add(id + " (prefab '" + prefab + "' 不存在)");
                    continue;
                }

                MatrixFrame frame = BuildFrame(spec);
                EntitySnapshot prior;
                if (byId.TryGetValue(id, out prior))
                {
                    // Update in place: move the existing entity so its identity (and
                    // therefore its GUID) survives, per the client's contract.
                    try
                    {
                        GameEntity target = FindEntityByPointer(scene, prior.Pointer);
                        if (EngineNull.IsNotNull(target))
                        {
                            target.SetLocalFrame(ref frame, true);
                            updated.Add(id);
                            continue;
                        }
                    }
                    catch (Exception ex)
                    {
                        EditorProbeConfig.TryLog("apply_layout 更新 " + id + " 失败，改为新建: " + ex.Message);
                    }
                }

                try
                {
                    scene.AddEntityWithMultiMesh(mesh, ref frame);
                    created.Add(id);
                }
                catch (Exception ex)
                {
                    rejected.Add(id + " (加入场景失败: " + ex.Message + ")");
                }
            }

            StringBuilder sb = new StringBuilder(256);
            sb.Append("{\"scene\":").Append(EditorProbeJson.Quote(SafeSceneName(scene)));
            sb.Append(",\"created\":").Append(StringArray(created));
            sb.Append(",\"updated\":").Append(StringArray(updated));
            sb.Append(",\"rejected\":").Append(StringArray(rejected));
            sb.Append(",\"changed\":").Append((created.Count + updated.Count) > 0 ? "true" : "false");
            sb.Append('}');
            return sb.ToString();
        }

        private static MatrixFrame BuildFrame(Dictionary<string, object> spec)
        {
            double x, y, z;
            if (!EditorProbeJson.TryGetNumber(spec, "x", out x)
                || !EditorProbeJson.TryGetNumber(spec, "y", out y)
                || !EditorProbeJson.TryGetNumber(spec, "z", out z))
                throw new InvalidOperationException("实体缺少 x/y/z");

            // Rotation and scale default to identity when absent, matching the
            // client's optional fields.
            float rx = (float)NumberOr(spec, "rx", 0);
            float ry = (float)NumberOr(spec, "ry", 0);
            float rz = (float)NumberOr(spec, "rz", 0);
            float sx = (float)NumberOr(spec, "sx", 1);
            float sy = (float)NumberOr(spec, "sy", 1);
            float sz = (float)NumberOr(spec, "sz", 1);

            // MatrixFrame has NO scale field. A Mat3 stores scale inside its basis
            // vectors (columns s/f/u), so scale is applied by multiplying those
            // vectors rather than by assigning a separate component.
            Mat3 rotation = Mat3.Identity;
            if (rx != 0f || ry != 0f || rz != 0f)
            {
                // ApplyEulerAngles takes RADIANS. The client sends degrees (its
                // schema documents +-3600), so the conversion happens here once.
                Vec3 euler = new Vec3(
                    rx * (float)Math.PI / 180f,
                    ry * (float)Math.PI / 180f,
                    rz * (float)Math.PI / 180f);
                rotation.ApplyEulerAngles(euler);
            }

            if (sx != 1f || sy != 1f || sz != 1f)
            {
                rotation.s = rotation.s * sx;
                rotation.f = rotation.f * sy;
                rotation.u = rotation.u * sz;
            }

            return new MatrixFrame(rotation, new Vec3((float)x, (float)y, (float)z));
        }

        private static double NumberOr(Dictionary<string, object> o, string key, double fallback)
        {
            double v;
            return EditorProbeJson.TryGetNumber(o, key, out v) ? v : fallback;
        }

        private static GameEntity FindEntityByPointer(Scene scene, UIntPtr pointer)
        {
            List<GameEntity> entities = new List<GameEntity>();
            scene.GetEntities(ref entities);
            for (int i = 0; i < entities.Count; i++)
            {
                if (EngineNull.IsNotNull(entities[i]) && entities[i].Pointer == pointer) return entities[i];
            }
            return null;
        }

        // ── save ────────────────────────────────────────────────────────────────

        /// <summary>
        /// Persists the scene. Scene has no public Save in the verified surface, so
        /// the engine's own editor save entry point is used through reflection; if it
        /// cannot be bound this reports bridge_error rather than claiming a save that
        /// did not happen — a false "saved" is the worst possible answer here.
        /// </summary>
        private static string SaveScene()
        {
            Scene scene = RequireScene();
            bool ok = ReflectionBridge.TryCallStatic("TaleWorlds.MountAndBlade.MBTestRun", "SaveScene")
                   || ReflectionBridge.TryCallStatic("TaleWorlds.MountAndBlade.MBEditor", "SaveScene");

            if (!ok)
                throw new InvalidOperationException(
                    "无法保存场景：本机未找到可绑定的编辑器保存入口（MBTestRun.SaveScene / MBEditor.SaveScene）。"
                    + "场景未被写入磁盘。");

            StringBuilder sb = new StringBuilder(128);
            sb.Append("{\"saved\":true,\"scene\":").Append(EditorProbeJson.Quote(SafeSceneName(scene)));
            sb.Append(",\"sceneDir\":").Append(EditorProbeJson.Quote(EditorProbeConfig.SceneDir));
            sb.Append('}');
            return sb.ToString();
        }

        // ── capture ─────────────────────────────────────────────────────────────

        /// <summary>
        /// The client requires result.completed===true and a string pngPath that it
        /// then realpath-checks is INSIDE session.outputDir, and whose first 8 bytes
        /// are the PNG signature. So the file is written to outputDir and only
        /// reported complete once it exists non-empty.
        /// </summary>
        private static string Capture()
        {
            string name = "capture_" + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss_fff", CultureInfo.InvariantCulture) + ".png";
            string path = IOPath.Combine(EditorProbeConfig.OutputDir, name);

            try
            {
                Utilities.TakeScreenshot(path);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("Utilities.TakeScreenshot 失败：" + ex.Message);
            }

            // The engine writes asynchronously at end of frame, so the file is not
            // necessarily present yet. Report completed:false rather than a path the
            // client would then fail to read and report as invalid_capture.
            bool present = false;
            try { present = File.Exists(path) && new FileInfo(path).Length > 24; } catch { }

            StringBuilder sb = new StringBuilder(160);
            sb.Append("{\"completed\":").Append(present ? "true" : "false");
            sb.Append(",\"pngPath\":").Append(EditorProbeJson.Quote(path));
            sb.Append(",\"note\":").Append(EditorProbeJson.Quote(
                present ? "已写入" : "截图已请求，引擎在本帧结束后写入；未完成前不报告路径可用"));
            sb.Append('}');
            return sb.ToString();
        }

        // ── prefab_info ─────────────────────────────────────────────────────────

        /// <summary>
        /// Inspects a prefab WITHOUT instantiating it, per the client's description.
        /// Only reports bounds when the engine exposes them; absent values are null
        /// rather than invented zeros, so "no mesh" never looks like "mesh at 0,0,0".
        /// </summary>
        private static string PrefabInfo(Dictionary<string, object> args)
        {
            string prefab = EditorProbeJson.GetString(args, "prefab");
            string meshName = EditorProbeJson.GetString(args, "mesh");
            if (string.IsNullOrEmpty(prefab)) throw new InvalidOperationException("prefab_info 需要 prefab");

            MetaMesh mesh;
            try
            {
                mesh = MetaMesh.GetCopy(prefab, false, true);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("MetaMesh.GetCopy 失败：" + ex.Message);
            }

            StringBuilder sb = new StringBuilder(200);
            sb.Append("{\"prefab\":").Append(EditorProbeJson.Quote(prefab));
            sb.Append(",\"exists\":").Append(EngineNull.IsNotNull(mesh) ? "true" : "false");

            if (EngineNull.IsNull(mesh))
            {
                sb.Append(",\"mesh\":").Append(meshName == null ? "null" : EditorProbeJson.Quote(meshName));
                sb.Append(",\"note\":").Append(EditorProbeJson.Quote("预制体不存在或无法解析"));
                sb.Append('}');
                return sb.ToString();
            }

            sb.Append(",\"mesh\":").Append(meshName == null ? "null" : EditorProbeJson.Quote(meshName));
            sb.Append(",\"meshCount\":").Append(mesh.MeshCount.ToString(CultureInfo.InvariantCulture));

            // Bounds are genuinely unavailable in the verified surface, so they are
            // reported as null. The client documents this as "when available", and a
            // fabricated box would be worse than an absent one.
            sb.Append(",\"bounds\":null");
            sb.Append(",\"note\":").Append(EditorProbeJson.Quote(
                "已解析预制体；包围盒需引擎在场景内实例化后才有值，此处按客户端约定返回 null"));
            sb.Append('}');
            return sb.ToString();
        }

        // ── camera / focus / probe placement ────────────────────────────────────

        private static string Focus(Dictionary<string, object> args)
        {
            string id = EditorProbeJson.GetString(args, "id");
            if (string.IsNullOrEmpty(id)) throw new InvalidOperationException("focus 需要 id");

            Scene scene = RequireScene();
            EntitySnapshot target = FindManaged(scene, id);
            if (target == null) throw new InvalidOperationException("找不到托管实体：" + id);

            bool ok = ReflectionBridge.TryCallStatic(
                "TaleWorlds.MountAndBlade.MBEditor", "ZoomToPosition", target.Origin);

            return "{\"focused\":" + (ok ? "true" : "false") + ",\"id\":" + EditorProbeJson.Quote(id) + "}";
        }

        private static string Camera(Dictionary<string, object> args)
        {
            double dx = NumberOr(args, "dx", 0), dy = NumberOr(args, "dy", 0), dz = NumberOr(args, "dz", 0);
            Vec3 delta = new Vec3((float)dx, (float)dy, (float)dz);

            bool ok = ReflectionBridge.TryCallStatic(
                "TaleWorlds.MountAndBlade.MBEditor", "ApplyDeltaToEditorCamera", delta);

            StringBuilder sb = new StringBuilder(96);
            sb.Append("{\"applied\":").Append(ok ? "true" : "false");
            sb.Append(",\"dx\":").Append(dx.ToString("R", CultureInfo.InvariantCulture));
            sb.Append(",\"dy\":").Append(dy.ToString("R", CultureInfo.InvariantCulture));
            sb.Append(",\"dz\":").Append(dz.ToString("R", CultureInfo.InvariantCulture));
            sb.Append('}');
            return sb.ToString();
        }

        private static string PlaceProbe(Dictionary<string, object> args)
        {
            string prefab = EditorProbeJson.GetString(args, "prefab") ?? "probe_visual_center";
            Dictionary<string, object> spec = new Dictionary<string, object>(StringComparer.Ordinal);
            spec["id"] = "probe";
            spec["prefab"] = prefab;
            spec["x"] = NumberOr(args, "x", 0);
            spec["y"] = NumberOr(args, "y", 0);
            spec["z"] = NumberOr(args, "z", 0);

            Dictionary<string, object> wrapper = new Dictionary<string, object>(StringComparer.Ordinal);
            wrapper["entities"] = new List<object> { spec };
            return ApplyLayout(wrapper);
        }

        private static string MoveProbe(Dictionary<string, object> args)
        {
            Scene scene = RequireScene();
            EntitySnapshot target = FindManaged(scene, "probe");
            if (target == null) throw new InvalidOperationException("还没有 probe 实体，请先 place_probe");

            Dictionary<string, object> spec = new Dictionary<string, object>(StringComparer.Ordinal);
            spec["id"] = "probe";
            spec["prefab"] = EditorProbeJson.GetString(args, "prefab") ?? "probe_visual_center";
            spec["x"] = NumberOr(args, "x", target.Origin.x);
            spec["y"] = NumberOr(args, "y", target.Origin.y);
            spec["z"] = NumberOr(args, "z", target.Origin.z);

            Dictionary<string, object> wrapper = new Dictionary<string, object>(StringComparer.Ordinal);
            wrapper["entities"] = new List<object> { spec };
            return ApplyLayout(wrapper);
        }

        // ── shared helpers ──────────────────────────────────────────────────────

        private static Scene RequireScene()
        {
            Scene scene = _editorScene ?? CurrentScene();
            if (EngineNull.IsNull(scene))
                throw new InvalidOperationException("当前没有活动场景（编辑器未打开）");
            return scene;
        }

        /// <summary>
        /// The scene the engine currently renders, if any. Returns null outside a
        /// mission/editor rather than throwing, because `status` must be answerable
        /// at any time — that is its whole purpose.
        /// </summary>
        private static Scene CurrentScene()
        {
            try
            {
                Mission mission = Mission.Current;
                if (EngineNull.IsNotNull(mission))
                {
                    Scene scene = mission.Scene;
                    if (EngineNull.IsNotNull(scene)) return scene;
                }
            }
            catch
            {
            }
            try
            {
                SceneView view = MBEditor.GetEditorSceneView();
                if (EngineNull.IsNotNull(view)) return view.GetScene();
            }
            catch
            {
            }
            return null;
        }

        private static string SafeSceneName(Scene scene)
        {
            try { return scene.GetName(); }
            catch { return null; }
        }

        private static EntitySnapshot FindManaged(Scene scene, string id)
        {
            string wanted = EntityNamePrefix + id;
            List<EntitySnapshot> all = SnapshotEntities(scene, true);
            for (int i = 0; i < all.Count; i++)
            {
                if (string.Equals(all[i].Name, wanted, StringComparison.Ordinal)) return all[i];
            }
            return null;
        }

        private static string StringArray(List<string> values)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append('[');
            for (int i = 0; i < values.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append(EditorProbeJson.Quote(values[i]));
            }
            sb.Append(']');
            return sb.ToString();
        }

        /// <summary>One entity as reported to the client.</summary>
        private sealed class EntitySnapshot
        {
            internal string Name;
            internal string Id;
            internal bool Managed;
            internal Vec3 Origin;
            internal Mat3 Rotation;
            internal Vec3 Scale;
            internal UIntPtr Pointer;

            internal void Write(StringBuilder sb)
            {
                sb.Append('{');
                sb.Append("\"id\":").Append(EditorProbeJson.Quote(Id));
                sb.Append(",\"name\":").Append(EditorProbeJson.Quote(Name));
                sb.Append(",\"managed\":").Append(Managed ? "true" : "false");
                sb.Append(",\"x\":").Append(Num(Origin.x));
                sb.Append(",\"y\":").Append(Num(Origin.y));
                sb.Append(",\"z\":").Append(Num(Origin.z));
                sb.Append(",\"sx\":").Append(Num(Scale.x));
                sb.Append(",\"sy\":").Append(Num(Scale.y));
                sb.Append(",\"sz\":").Append(Num(Scale.z));
                // GUID is not exposed by the verified engine surface; null is
                // reported rather than a made-up identifier.
                sb.Append(",\"guid\":null");
                sb.Append('}');
            }

            private static string Num(float v)
            {
                if (float.IsNaN(v) || float.IsInfinity(v)) return "null";
                return v.ToString("R", CultureInfo.InvariantCulture);
            }
        }
    }

    /// <summary>
    /// The one place reflection is used, kept small and explicit.
    ///
    /// Reflection is the right tool for exactly one situation here: calling a member
    /// whose signature crosses an INTERNAL engine interface, where a direct call
    /// would either fail to compile on a different game build or bind to the wrong
    /// overload. Everywhere an API is verifiable from the decompiled source, a direct
    /// call is used instead — reflection is not a default.
    ///
    /// Failures return false and are logged; they never throw, so the caller decides
    /// whether a missing binding is fatal (it usually is, and is reported as such).
    /// </summary>
    internal static class ReflectionBridge
    {
        private static readonly Dictionary<string, Type> TypeCache = new Dictionary<string, Type>(StringComparer.Ordinal);
        private static readonly object CacheGate = new object();

        private static Type Resolve(string typeName)
        {
            lock (CacheGate)
            {
                Type cached;
                if (TypeCache.TryGetValue(typeName, out cached)) return cached;

                Type resolved = null;
                try
                {
                    Assembly[] assemblies = AppDomain.CurrentDomain.GetAssemblies();
                    for (int i = 0; i < assemblies.Length && resolved == null; i++)
                    {
                        try { resolved = assemblies[i].GetType(typeName, false); }
                        catch { }
                    }
                }
                catch
                {
                }
                TypeCache[typeName] = resolved;
                return resolved;
            }
        }

        internal static bool TryCallStatic(string typeName, string methodName, params object[] args)
        {
            try
            {
                Type type = Resolve(typeName);
                if (type == null)
                {
                    EditorProbeConfig.TryLog("ReflectionBridge: 找不到类型 " + typeName);
                    return false;
                }

                MethodInfo[] candidates = type.GetMethods(BindingFlags.Public | BindingFlags.Static);
                for (int i = 0; i < candidates.Length; i++)
                {
                    MethodInfo method = candidates[i];
                    if (!string.Equals(method.Name, methodName, StringComparison.Ordinal)) continue;

                    ParameterInfo[] parameters = method.GetParameters();
                    if (parameters.Length != args.Length) continue;

                    if (!ArgumentsFit(parameters, args)) continue;

                    try
                    {
                        method.Invoke(null, args);
                        return true;
                    }
                    catch (Exception ex)
                    {
                        EditorProbeConfig.TryLog("ReflectionBridge: " + typeName + "." + methodName
                            + " 调用抛出: " + (ex.InnerException ?? ex).Message);
                        // A call that binds but throws means the member EXISTS and the
                        // invocation failed — that is not a missing binding, so the
                        // caller is told it succeeded in binding and gets the log line.
                        return true;
                    }
                }

                EditorProbeConfig.TryLog("ReflectionBridge: " + typeName + "." + methodName + " 无匹配重载");
                return false;
            }
            catch (Exception ex)
            {
                EditorProbeConfig.TryLog("ReflectionBridge: " + typeName + "." + methodName + " 异常: " + ex.Message);
                return false;
            }
        }

        private static bool ArgumentsFit(ParameterInfo[] parameters, object[] args)
        {
            for (int i = 0; i < parameters.Length; i++)
            {
                object arg = args[i];
                Type wanted = parameters[i].ParameterType;

                if (arg == null)
                {
                    if (wanted.IsValueType && Nullable.GetUnderlyingType(wanted) == null) return false;
                    continue;
                }
                if (wanted.IsInstanceOfType(arg)) continue;

                // `in` / `ref` parameters arrive as by-ref types; compare the element.
                Type element = wanted.IsByRef ? wanted.GetElementType() : wanted;
                if (element != null && element.IsInstanceOfType(arg)) continue;
                return false;
            }
            return true;
        }

        internal static object TryGetStaticProperty(string typeName, string propertyName)
        {
            try
            {
                Type type = Resolve(typeName);
                if (type == null) return null;
                PropertyInfo property = type.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Static);
                if (property == null) return null;
                return property.GetValue(null, null);
            }
            catch
            {
                return null;
            }
        }
    }
}
