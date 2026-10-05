// ArmoryTool: the Editing Kit side of Unified Armory, built on ManagedBlam.
//
//   ArmoryTool extract <EditingKitPath> <jobs.json>
//       Dump render models, the shaders they use, and export those shaders' bitmaps
//       (via the kit's tool.exe) into the Unified Armory cache.
//   ArmoryTool dump <EditingKitPath> <tag path with extension> <out.json>
//       Dump any tag as a generic field tree (for debugging field names).
//   ArmoryTool apply <EditingKitPath> <plan.json> --stage pre|post [--dry-run]
//       Write a build plan's tag edits.
//
// The tool deliberately knows nothing about tag layouts. Dumps are generic trees of
// {n: name, t: kind, v: value, e: elements} that the Python side interprets, and edits
// find fields by name (recursive, case-insensitive) and write values through each
// field's Data/Path property by reflection. That keeps one binary working across the
// H2, H3, ODST, Reach and H4 kits, whose structs nest and name things differently.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Web.Script.Serialization;
using Bungie;
using Bungie.Tags;

internal static class Program
{
    static string EK;
    static bool DryRun;
    static Dictionary<string, string> Fields = new Dictionary<string, string>();
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 1000 };

    static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("usage:\n  ArmoryTool extract <EK> <jobs.json>\n  ArmoryTool dump <EK> <tag> <out.json>\n  ArmoryTool apply <EK> <plan.json> --stage pre|post [--dry-run]");
            return 1;
        }
        EK = Path.GetFullPath(args[1]);
        DryRun = args.Contains("--dry-run");
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string candidate = Path.Combine(EK, "bin", new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
        };
        try
        {
            return Run(args);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("error: " + ex.Message);
            return 2;
        }
    }

    // Kept separate from Main so ManagedBlam types aren't touched before AssemblyResolve is hooked.
    static int Run(string[] args)
    {
        Environment.CurrentDirectory = EK;
        ManagedBlamSystem.InitializeProject(InitializationType.TagsOnly, EK);
        try
        {
            switch (args[0])
            {
                case "extract": return Extract(args[2]);
                case "dump": File.WriteAllText(args[3], Json.Serialize(DumpTag(args[2]))); return 0;
                case "apply":
                    int i = Array.IndexOf(args, "--stage");
                    if (i < 0 || i + 1 >= args.Length) throw new ArgumentException("apply needs --stage pre|post");
                    return Apply(args[2], args[i + 1]);
                default: throw new ArgumentException("unknown command " + args[0]);
            }
        }
        finally
        {
            ManagedBlamSystem.Stop();
        }
    }

    // ---------------------------------------------------------------- extract

    static int Extract(string jobsPath)
    {
        var jobs = (Dictionary<string, object>)Json.DeserializeObject(File.ReadAllText(jobsPath));
        string bitmapCmd = (string)jobs["bitmap_command"];
        int failures = 0;
        foreach (Dictionary<string, object> job in (object[])jobs["jobs"])
        {
            string tag = (string)job["tag"], outDir = (string)job["out"];
            Console.WriteLine($"extract {tag}");
            try
            {
                Directory.CreateDirectory(Path.Combine(outDir, "shaders"));
                Directory.CreateDirectory(Path.Combine(outDir, "bitmaps"));
                var model = DumpTag(tag);
                File.WriteAllText(Path.Combine(outDir, "model.json"), Json.Serialize(model));

                var shaders = References(model).Where(r => !r.EndsWith(".bitmap", StringComparison.OrdinalIgnoreCase)
                    && (r.Contains(".shader") || r.EndsWith(".material", StringComparison.OrdinalIgnoreCase))).Distinct().ToList();
                var bitmaps = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var shader in shaders)
                {
                    try
                    {
                        var dump = DumpTag(shader);
                        File.WriteAllText(Path.Combine(outDir, "shaders", Slug(shader) + ".json"), Json.Serialize(dump));
                        foreach (var b in References(dump).Where(r => r.EndsWith(".bitmap", StringComparison.OrdinalIgnoreCase)))
                            bitmaps.Add(b);
                    }
                    catch (Exception ex) { Console.Error.WriteLine($"  shader {shader}: {ex.Message}"); }
                }
                foreach (var bitmap in bitmaps)
                {
                    string outFile = Path.Combine(outDir, "bitmaps", Slug(bitmap) + ".tga");
                    string cmd = bitmapCmd.Replace("{tag}", bitmap.Substring(0, bitmap.LastIndexOf('.'))).Replace("{out}", outFile);
                    var p = Process.Start(new ProcessStartInfo("cmd.exe", "/c " + cmd) { WorkingDirectory = EK, UseShellExecute = false });
                    p.WaitForExit();
                    if (p.ExitCode != 0 || !File.Exists(outFile))
                        Console.Error.WriteLine($"  bitmap {bitmap}: export failed (check bitmap_export in the game's data file)");
                }
                Console.WriteLine($"  ok: {shaders.Count} shaders, {bitmaps.Count} bitmaps");
            }
            catch (Exception ex)
            {
                failures++;
                Console.Error.WriteLine($"  FAIL {tag}: {ex.Message}");
            }
        }
        return failures == 0 ? 0 : 2;
    }

    static string Slug(string tagPath)
    {
        string p = tagPath.Replace('\\', '/');
        int dot = p.LastIndexOf('.');
        if (dot > p.LastIndexOf('/')) p = p.Substring(0, dot);
        return p.Trim('/').Replace("/", "__");
    }

    static IEnumerable<string> References(object node)
    {
        if (node is Dictionary<string, object> d)
        {
            if (d.TryGetValue("t", out var t) && (string)t == "ref" && d.TryGetValue("v", out var v) && v is string s && s.Length > 0)
                yield return s;
            foreach (var child in d.Values)
                foreach (var r in References(child)) yield return r;
        }
        else if (node is IEnumerable e && !(node is string))
            foreach (var item in e)
                foreach (var r in References(item)) yield return r;
    }

    // ---------------------------------------------------------------- dump

    static Dictionary<string, object> DumpTag(string relative)
    {
        using (var tag = new TagFile(ToTagPath(relative)))
            return new Dictionary<string, object> { ["tag"] = relative, ["fields"] = tag.Fields.Select(DumpField).ToList() };
    }

    static Dictionary<string, object> DumpField(TagField f)
    {
        var d = new Dictionary<string, object> { ["n"] = f.FieldName };
        var elements = f.GetType().GetProperty("Elements")?.GetValue(f) as IEnumerable;
        if (elements != null)
        {
            string type = f.GetType().Name;
            d["t"] = type.Contains("Block") ? "block" : type.Contains("Array") ? "array" : "struct";
            d["e"] = elements.Cast<TagElement>().Select(el => el.Fields.Select(DumpField).ToList()).ToList();
        }
        else if (f is TagFieldReference r)
        {
            d["t"] = "ref";
            d["v"] = RefPath(r);
        }
        else
        {
            d["t"] = "value";
            d["v"] = Plain(f.GetType().GetProperty("Data")?.GetValue(f));
        }
        return d;
    }

    static string RefPath(TagFieldReference r)
    {
        var p = r.Path;
        if (p == null) return "";
        var t = p.GetType();
        var full = t.GetProperty("RelativePathWithExtension")?.GetValue(p) as string;
        if (full != null) return full;
        var rel = t.GetProperty("RelativePath")?.GetValue(p) as string;
        var ext = t.GetProperty("Extension")?.GetValue(p) as string;
        return rel != null ? (string.IsNullOrEmpty(ext) ? rel : rel + "." + ext) : p.ToString();
    }

    // Turn a field's Data into something JSON-friendly: numbers, strings, lists, or {x,y,z...} objects.
    static object Plain(object v, int depth = 0)
    {
        if (v == null || v is string || v is bool || v.GetType().IsPrimitive || v is decimal) return v;
        if (v is Enum) return v.ToString();
        if (depth > 3) return v.ToString();
        if (v is IEnumerable e) return e.Cast<object>().Select(x => Plain(x, depth + 1)).ToList();
        var props = v.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance).Where(p => p.GetIndexParameters().Length == 0).ToList();
        var fields = v.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance).ToList();
        if (props.Count + fields.Count == 0) return v.ToString();
        var o = new Dictionary<string, object>();
        foreach (var p in props) { try { o[p.Name.ToLowerInvariant()] = Plain(p.GetValue(v), depth + 1); } catch { } }
        foreach (var fi in fields) o[fi.Name.ToLowerInvariant()] = Plain(fi.GetValue(v), depth + 1);
        return o;
    }

    // ---------------------------------------------------------------- apply

    static int Apply(string planPath, string stage)
    {
        var plan = (Dictionary<string, object>)Json.DeserializeObject(File.ReadAllText(planPath));
        if ((string)plan["format"] != "unified-armory-plan/2")
            throw new InvalidDataException("not a unified-armory-plan/2 file; rebuild it with the current Unified Armory");
        Fields = ((Dictionary<string, object>)plan["fields"]).ToDictionary(kv => kv.Key, kv => (string)kv.Value);
        var ops = ((object[])((Dictionary<string, object>)plan["stages"])[stage]).Cast<Dictionary<string, object>>().ToList();
        Console.WriteLine($"{plan["name"]} ({stage}): {ops.Count} edits{(DryRun ? " (dry run)" : "")}");
        int failures = 0;
        foreach (var op in ops)
        {
            try
            {
                Console.WriteLine("  " + Describe(op));
                Do(op);
            }
            catch (Exception ex)
            {
                failures++;
                Console.Error.WriteLine($"    FAIL: {ex.Message}");
            }
        }
        return failures == 0 ? 0 : 2;
    }

    static string Describe(Dictionary<string, object> op)
    {
        switch ((string)op["op"])
        {
            case "copy_tag": return $"copy {op["from"]} -> {op["to"]}";
            case "clone_shader": return $"shader {op["to"]} (from {op["template"]})";
            case "set_reference": return $"{op["tag"]}: {op["field"]} = {op["value"]}";
            case "set_change_color": return $"{op["tag"]}: change color {op["index"]}";
            default: return (string)op["op"];
        }
    }

    static void Do(Dictionary<string, object> op)
    {
        switch ((string)op["op"])
        {
            case "copy_tag": CopyTag((string)op["from"], (string)op["to"]); break;
            case "clone_shader": CloneShader(op); break;
            case "set_reference": Edit((string)op["tag"], t => Find<TagFieldReference>(t.Fields, (string)op["field"]).Path = ToTagPath((string)op["value"])); break;
            case "set_change_color": Edit((string)op["tag"], t => SetChangeColor(t, op)); break;
            default: throw new NotSupportedException($"unknown op {op["op"]}");
        }
    }

    static string TagFilePath(string relative) => Path.Combine(EK, "tags", relative);

    static void CopyTag(string from, string to)
    {
        string src = TagFilePath(from), dst = TagFilePath(to);
        if (!File.Exists(src)) throw new FileNotFoundException("source tag not found", src);
        if (DryRun) return;
        Directory.CreateDirectory(Path.GetDirectoryName(dst));
        File.Copy(src, dst, true);
    }

    static void Edit(string tagPath, Action<TagFile> change)
    {
        using (var tag = new TagFile(ToTagPath(tagPath)))
        {
            change(tag);
            if (!DryRun) tag.Save();
        }
    }

    // Copy the target's armor shader and point its bitmap parameters at the ported textures.
    static void CloneShader(Dictionary<string, object> op)
    {
        string to = (string)op["to"];
        CopyTag((string)op["template"], to);
        var bitmaps = (Dictionary<string, object>)op["bitmaps"];
        if (DryRun || bitmaps.Count == 0) return;
        Edit(to, tag =>
        {
            var parameters = Search(tag.Fields, Fields["shader_parameters"]).OfType<TagFieldBlock>().FirstOrDefault();
            foreach (var kv in bitmaps)
            {
                var path = ToTagPath((string)kv.Value);
                // Shaders with a fixed bitmap field of this name (e.g. Halo CE style "base map").
                var direct = Search(tag.Fields, kv.Key).OfType<TagFieldReference>().FirstOrDefault();
                if (direct != null) { direct.Path = path; continue; }
                if (parameters == null) throw new MissingFieldException($"no '{Fields["shader_parameters"]}' block or '{kv.Key}' field in template");
                TagElement param = null;
                foreach (TagElement el in parameters.Elements)
                    if (string.Equals(Convert.ToString(GetData(Find<TagField>(el.Fields, Fields["shader_parameter_name"]))), kv.Key, StringComparison.OrdinalIgnoreCase))
                        param = el;
                if (param == null)
                {
                    // Template lacks this map: add a parameter (type left at its default, the bitmap type in H3-family shaders).
                    param = parameters.AddElement();
                    SetData(Find<TagField>(param.Fields, Fields["shader_parameter_name"]), kv.Key);
                }
                Find<TagFieldReference>(param.Fields, Fields["shader_parameter_bitmap"]).Path = path;
            }
        });
    }

    // Pin change color N to one permutation whose lower and upper bounds are the exact color.
    static void SetChangeColor(TagFile tag, Dictionary<string, object> op)
    {
        int index = Convert.ToInt32(op["index"]);
        float[] rgb = ((object[])op["color"]).Select(Convert.ToSingle).ToArray();
        var changeColors = Find<TagFieldBlock>(tag.Fields, Fields["change_colors"]);
        while (changeColors.Elements.Count <= index) changeColors.AddElement();
        var perms = Find<TagFieldBlock>(changeColors.Elements[index].Fields, Fields["change_color_permutations"]);
        perms.RemoveAllElements();
        var perm = perms.AddElement();
        SetData(Find<TagField>(perm.Fields, Fields["permutation_weight"]), 1.0f);
        SetData(Find<TagField>(perm.Fields, Fields["color_lower_bound"]), rgb);
        SetData(Find<TagField>(perm.Fields, Fields["color_upper_bound"]), rgb);
    }

    // ---------------------------------------------------------------- helpers

    static TagPath ToTagPath(string relative)
    {
        string ext = Path.GetExtension(relative).TrimStart('.');
        return TagPath.FromPathAndExtension(relative.Substring(0, relative.Length - ext.Length - 1), ext);
    }

    static T Find<T>(IEnumerable<TagField> fields, string name) where T : TagField =>
        Search(fields, name).OfType<T>().FirstOrDefault()
        ?? throw new MissingFieldException($"field '{name}' ({typeof(T).Name}) not found; check the field names in the game's data file");

    // Breadth-first through nested structs/arrays (not blocks), so the shallowest match wins.
    static IEnumerable<TagField> Search(IEnumerable<TagField> fields, string name)
    {
        var queue = new Queue<TagField>(fields);
        while (queue.Count > 0)
        {
            var f = queue.Dequeue();
            if (string.Equals(f.FieldName?.Trim(), name, StringComparison.OrdinalIgnoreCase)
                || string.Equals(f.DisplayName?.Trim(), name, StringComparison.OrdinalIgnoreCase))
                yield return f;
            if (!(f is TagFieldBlock) && f.GetType().GetProperty("Elements")?.GetValue(f) is IEnumerable els)
                foreach (TagElement el in els)
                    foreach (var child in el.Fields) queue.Enqueue(child);
        }
    }

    static object GetData(TagField f) => f.GetType().GetProperty("Data")?.GetValue(f);

    static void SetData(TagField f, object value)
    {
        if (DryRun) return;
        var prop = f.GetType().GetProperty("Data") ?? throw new NotSupportedException($"{f.FieldName}: no Data property on {f.GetType().Name}");
        var target = prop.PropertyType;
        if (target.IsArray && value is float[] arr)
        {
            var dst = Array.CreateInstance(target.GetElementType(), arr.Length);
            for (int i = 0; i < arr.Length; i++) dst.SetValue(Convert.ChangeType(arr[i], target.GetElementType()), i);
            prop.SetValue(f, dst);
        }
        else
            prop.SetValue(f, target == typeof(string) ? value.ToString() : Convert.ChangeType(value, target));
    }
}
