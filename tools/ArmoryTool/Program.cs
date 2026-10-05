// ArmoryTool: the Editing Kit side of Unified Armory, built on ManagedBlam.
//
//   ArmoryTool extract <EditingKitPath> <jobs.json>
//   ArmoryTool dump    <EditingKitPath> <tag path with extension> <out.json>
//   ArmoryTool apply   <EditingKitPath> <plan.json> --stage pre|post [--dry-run]
//
// ManagedBlam.dll is loaded at runtime from <kit>\bin and used purely through reflection,
// so this compiles without any kit installed and one binary serves the H2,
// H3, ODST, Reach and H4 kits. It knows nothing about tag layouts: dumps are generic
// field trees for the Python side to interpret, and edits find fields by name.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Web.Script.Serialization;

internal static class Program
{
    static string EK;
    static bool DryRun;
    static Assembly MB;
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
        try
        {
            LoadManagedBlam();
            try { return Run(args); }
            finally { Call(MBType("Bungie.ManagedBlamSystem"), "Stop"); }
        }
        catch (Exception ex)
        {
            while (ex is TargetInvocationException && ex.InnerException != null) ex = ex.InnerException;
            Console.Error.WriteLine("error: " + ex.Message);
            return 2;
        }
    }

    static void LoadManagedBlam()
    {
        string bin = Path.Combine(EK, "bin");
        string dll = Path.Combine(bin, "ManagedBlam.dll");
        if (!File.Exists(dll)) throw new FileNotFoundException($"ManagedBlam.dll not found in {bin}; is this an MCC Editing Kit folder?");
        Environment.SetEnvironmentVariable("PATH", bin + ";" + Environment.GetEnvironmentVariable("PATH"));
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string candidate = Path.Combine(bin, new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
        };
        MB = Assembly.LoadFrom(dll);
        Environment.CurrentDirectory = EK;
        var sys = MBType("Bungie.ManagedBlamSystem");
        var init = sys.GetMethods().First(m => m.Name == "InitializeProject");
        var ps = init.GetParameters();
        var argv = new object[ps.Length];
        for (int i = 0; i < ps.Length; i++)
        {
            if (ps[i].ParameterType.IsEnum) argv[i] = Enum.Parse(ps[i].ParameterType, "TagsOnly");
            else if (ps[i].ParameterType == typeof(string)) argv[i] = EK;
            else argv[i] = ps[i].HasDefaultValue ? ps[i].DefaultValue : null;
        }
        init.Invoke(null, argv);
    }

    static Type MBType(string name) => MB.GetType(name, true);
    static object Call(Type t, string method, params object[] a) => t.GetMethod(method, a.Select(x => x.GetType()).ToArray()).Invoke(null, a);

    static int Run(string[] args)
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

    // ---------------------------------------------------------------- ManagedBlam access

    static object ToTagPath(string relative)
    {
        string ext = Path.GetExtension(relative).TrimStart('.');
        return Call(MBType("Bungie.Tags.TagPath"), "FromPathAndExtension", relative.Substring(0, relative.Length - ext.Length - 1), ext);
    }

    static object OpenTag(string relative) => Activator.CreateInstance(MBType("Bungie.Tags.TagFile"), ToTagPath(relative));

    static object Invoke(object target, string method) =>
        target.GetType().GetMethod(method, System.Type.EmptyTypes).Invoke(target, null);

    static IEnumerable<object> FieldsOf(object owner) =>
        ((IEnumerable)owner.GetType().GetProperty("Fields").GetValue(owner)).Cast<object>();
    static IEnumerable<object> ElementsOf(object field) =>
        (field.GetType().GetProperty("Elements")?.GetValue(field) as IEnumerable)?.Cast<object>() ?? Enumerable.Empty<object>();
    static bool IsBlock(object f) => f.GetType().Name.Contains("Block");
    static bool IsReference(object f) => f.GetType().Name.Contains("Reference");
    static bool HasElements(object f) => f.GetType().GetProperty("Elements") != null;
    static string NameOf(object f) => f.GetType().GetProperty("FieldName")?.GetValue(f) as string ?? "";
    static string DisplayOf(object f) => f.GetType().GetProperty("DisplayName")?.GetValue(f) as string ?? "";

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
                    catch (Exception ex) { Console.Error.WriteLine($"  shader {shader}: {Inner(ex).Message}"); }
                }
                foreach (var bitmap in bitmaps)
                {
                    string outFile = Path.Combine(outDir, "bitmaps", Slug(bitmap) + ".tga");
                    string cmd = bitmapCmd.Replace("{tag}", bitmap.Substring(0, bitmap.LastIndexOf('.'))).Replace("{out}", outFile);
                    var p = Process.Start(new ProcessStartInfo("cmd.exe", "/c " + cmd) { WorkingDirectory = EK, UseShellExecute = false });
                    p.WaitForExit();
                    if (p.ExitCode != 0 || !File.Exists(outFile))
                        Console.Error.WriteLine($"  bitmap {bitmap}: export failed");
                }
                // Written last: its presence marks the extraction as complete.
                File.WriteAllText(Path.Combine(outDir, "model.json"), Json.Serialize(model));
                Console.WriteLine($"  ok: {shaders.Count} shaders, {bitmaps.Count} bitmaps");
            }
            catch (Exception ex)
            {
                failures++;
                Console.Error.WriteLine($"  FAIL {tag}: {Inner(ex).Message}");
            }
        }
        return failures == 0 ? 0 : 2;
    }

    static Exception Inner(Exception ex) { while (ex is TargetInvocationException && ex.InnerException != null) ex = ex.InnerException; return ex; }

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
        object tag = OpenTag(relative);
        try { return new Dictionary<string, object> { ["tag"] = relative, ["fields"] = FieldsOf(tag).Select(f => (object)DumpField(f)).ToList() }; }
        finally { ((IDisposable)tag).Dispose(); }
    }

    static Dictionary<string, object> DumpField(object f)
    {
        var d = new Dictionary<string, object> { ["n"] = NameOf(f) };
        if (HasElements(f))
        {
            string type = f.GetType().Name;
            d["t"] = type.Contains("Block") ? "block" : type.Contains("Array") ? "array" : "struct";
            d["e"] = ElementsOf(f).Select(el => FieldsOf(el).Select(x => (object)DumpField(x)).ToList()).ToList();
        }
        else if (IsReference(f))
        {
            d["t"] = "ref";
            d["v"] = RefPath(f.GetType().GetProperty("Path")?.GetValue(f));
        }
        else
        {
            d["t"] = "value";
            d["v"] = Plain(f.GetType().GetProperty("Data")?.GetValue(f));
        }
        return d;
    }

    static string RefPath(object p)
    {
        if (p == null) return "";
        var t = p.GetType();
        if (t.GetProperty("RelativePathWithExtension")?.GetValue(p) is string full) return full;
        var rel = t.GetProperty("RelativePath")?.GetValue(p) as string;
        var ext = t.GetProperty("Extension")?.GetValue(p) as string;
        return rel != null ? (string.IsNullOrEmpty(ext) ? rel : rel + "." + ext) : p.ToString();
    }

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
            try { Console.WriteLine("  " + Describe(op)); Do(op); }
            catch (Exception ex) { failures++; Console.Error.WriteLine($"    FAIL: {Inner(ex).Message}"); }
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
            case "add_script": return $"{op["tag"]}: script source {op["source"]}";
            default: return (string)op["op"];
        }
    }

    static void Do(Dictionary<string, object> op)
    {
        switch ((string)op["op"])
        {
            case "copy_tag": CopyTag((string)op["from"], (string)op["to"]); break;
            case "clone_shader": CloneShader(op); break;
            case "set_reference": Edit((string)op["tag"], t => SetPath(Find(FieldsOf(t), (string)op["field"], IsReference), (string)op["value"])); break;
            case "set_change_color": Edit((string)op["tag"], t => SetChangeColor(t, op)); break;
            case "add_script": Edit((string)op["tag"], t => AddScript(t, (string)op["source"])); break;
            default: throw new NotSupportedException($"unknown op {op["op"]}");
        }
    }

    static void CopyTag(string from, string to)
    {
        string src = Path.Combine(EK, "tags", from), dst = Path.Combine(EK, "tags", to);
        if (!File.Exists(src)) throw new FileNotFoundException("source tag not found", src);
        if (DryRun) return;
        Directory.CreateDirectory(Path.GetDirectoryName(dst));
        File.Copy(src, dst, true);
    }

    static void Edit(string tagPath, Action<object> change)
    {
        object tag = OpenTag(tagPath);
        try
        {
            change(tag);
            if (!DryRun) Invoke(tag, "Save");
        }
        finally { ((IDisposable)tag).Dispose(); }
    }

    static void SetPath(object referenceField, string value)
    {
        if (DryRun) return;
        referenceField.GetType().GetProperty("Path").SetValue(referenceField, ToTagPath(value));
    }

    static void CloneShader(Dictionary<string, object> op)
    {
        string to = (string)op["to"];
        CopyTag((string)op["template"], to);
        var bitmaps = (Dictionary<string, object>)op["bitmaps"];
        if (DryRun || bitmaps.Count == 0) return;
        Edit(to, tag =>
        {
            var top = FieldsOf(tag).ToList();
            var parameters = Search(top, Fields["shader_parameters"]).FirstOrDefault(IsBlock);
            foreach (var kv in bitmaps)
            {
                var direct = Search(top, kv.Key).FirstOrDefault(IsReference);
                if (direct != null) { SetPath(direct, (string)kv.Value); continue; }
                if (parameters == null) throw new MissingFieldException($"no '{Fields["shader_parameters"]}' block or '{kv.Key}' field in template");
                object param = null;
                foreach (var el in ElementsOf(parameters))
                    if (string.Equals(Convert.ToString(GetData(Find(FieldsOf(el), Fields["shader_parameter_name"], f => true))), kv.Key, StringComparison.OrdinalIgnoreCase))
                        param = el;
                if (param == null)
                {
                    param = Invoke(parameters, "AddElement");
                    SetData(Find(FieldsOf(param), Fields["shader_parameter_name"], f => true), kv.Key);
                }
                SetPath(Find(FieldsOf(param), Fields["shader_parameter_bitmap"], IsReference), (string)kv.Value);
            }
        });
    }

    // Reference the Unified Armory mission script from the scenario's script source files
    // (once), so tool compiles it into the level alongside the mission's own scripts.
    static void AddScript(object tag, string source)
    {
        object files = Find(FieldsOf(tag), Fields["scenario_source_files"], IsBlock);
        string wanted = source.Replace('/', '\\');
        foreach (var el in ElementsOf(files))
        {
            var r = FieldsOf(el).FirstOrDefault(IsReference);
            if (r != null && RefPath(r.GetType().GetProperty("Path")?.GetValue(r)).Equals(wanted, StringComparison.OrdinalIgnoreCase))
                return;
        }
        if (DryRun) return;
        object added = Invoke(files, "AddElement");
        var reference = FieldsOf(added).FirstOrDefault(IsReference)
            ?? throw new MissingFieldException("script source file entries have no reference field");
        SetPath(reference, wanted);
    }

    static void SetChangeColor(object tag, Dictionary<string, object> op)
    {
        int index = Convert.ToInt32(op["index"]);
        float[] rgb = ((object[])op["color"]).Select(Convert.ToSingle).ToArray();
        object changeColors = Find(FieldsOf(tag), Fields["change_colors"], IsBlock);
        while (ElementsOf(changeColors).Count() <= index) Invoke(changeColors, "AddElement");
        object element = ElementsOf(changeColors).ElementAt(index);
        object perms = Find(FieldsOf(element), Fields["change_color_permutations"], IsBlock);
        if (DryRun) return;
        Invoke(perms, "RemoveAllElements");
        object perm = Invoke(perms, "AddElement");
        SetData(Find(FieldsOf(perm), Fields["permutation_weight"], f => true), 1.0f);
        SetData(Find(FieldsOf(perm), Fields["color_lower_bound"], f => true), rgb);
        SetData(Find(FieldsOf(perm), Fields["color_upper_bound"], f => true), rgb);
    }

    // ---------------------------------------------------------------- helpers

    static object Find(IEnumerable<object> fields, string name, Func<object, bool> accept) =>
        Search(fields, name).FirstOrDefault(accept)
        ?? throw new MissingFieldException($"field '{name}' not found; check the field names in the game's data file");

    // Breadth-first through nested structs/arrays (not blocks), so the shallowest match wins.
    static IEnumerable<object> Search(IEnumerable<object> fields, string name)
    {
        var queue = new Queue<object>(fields);
        while (queue.Count > 0)
        {
            var f = queue.Dequeue();
            if (string.Equals(NameOf(f).Trim(), name, StringComparison.OrdinalIgnoreCase)
                || string.Equals(DisplayOf(f).Trim(), name, StringComparison.OrdinalIgnoreCase))
                yield return f;
            if (!IsBlock(f) && HasElements(f))
                foreach (var el in ElementsOf(f))
                    foreach (var child in FieldsOf(el)) queue.Enqueue(child);
        }
    }

    static object GetData(object f) => f.GetType().GetProperty("Data")?.GetValue(f);

    static void SetData(object f, object value)
    {
        if (DryRun) return;
        var prop = f.GetType().GetProperty("Data") ?? throw new NotSupportedException($"{NameOf(f)}: no Data property on {f.GetType().Name}");
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
