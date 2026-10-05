// Applies a Unified Armory plan.json to an MCC Editing Kit's tags via ManagedBlam.
//
//   ApplyPlan.exe <EditingKitPath> <plan.json> [--dry-run]
//
// Fields are found by name (recursively, case-insensitive) rather than by a fixed
// struct path, because nesting differs between engines. Names come from the plan's
// "fields" map, so a kit that labels a field differently can be fixed in the
// catalog without recompiling. Values are written through each field's Data/Path
// property via reflection, which keeps this independent of per-kit element class names.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Web.Script.Serialization;
using Bungie;
using Bungie.Tags;

internal static class Program
{
    static Dictionary<string, string> Fields;
    static bool DryRun;

    static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: ApplyPlan <EditingKitPath> <plan.json> [--dry-run]");
            return 1;
        }
        string ek = Path.GetFullPath(args[0]);
        DryRun = args.Contains("--dry-run");
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string candidate = Path.Combine(ek, "bin", new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
        };
        return Run(ek, args[1]);
    }

    static int Run(string ek, string planPath)
    {
        var plan = (Dictionary<string, object>)new JavaScriptSerializer { MaxJsonLength = int.MaxValue }
            .DeserializeObject(File.ReadAllText(planPath));
        if ((string)plan["format"] != "unified-armory-plan/1")
            throw new InvalidDataException("not a unified-armory-plan/1 file");
        Fields = ((Dictionary<string, object>)plan["fields"]).ToDictionary(kv => kv.Key, kv => (string)kv.Value);
        var edits = ((object[])plan["edits"]).Cast<Dictionary<string, object>>().ToList();
        Console.WriteLine($"{plan["name"]}: {edits.Count} edits{(DryRun ? " (dry run)" : "")}");

        Environment.CurrentDirectory = ek;
        ManagedBlamSystem.InitializeProject(InitializationType.TagsOnly, ek);
        int failures = 0;
        try
        {
            foreach (var group in edits.GroupBy(e => (string)e["tag"]))
            {
                try
                {
                    using (var tag = new TagFile(ToTagPath(group.Key)))
                    {
                        foreach (var edit in group) Apply(tag, edit);
                        if (!DryRun) tag.Save();
                    }
                    Console.WriteLine($"  ok    {group.Key}");
                }
                catch (Exception ex)
                {
                    failures++;
                    Console.Error.WriteLine($"  FAIL  {group.Key}: {ex.Message}");
                }
            }
        }
        finally
        {
            ManagedBlamSystem.Stop();
        }
        return failures == 0 ? 0 : 2;
    }

    static TagPath ToTagPath(string relative)
    {
        string ext = Path.GetExtension(relative).TrimStart('.');
        string path = relative.Substring(0, relative.Length - ext.Length - 1);
        return TagPath.FromPathAndExtension(path, ext);
    }

    static void Apply(TagFile tag, Dictionary<string, object> edit)
    {
        switch ((string)edit["op"])
        {
            case "set_change_color": SetChangeColor(tag, edit); break;
            case "set_model_variant": SetModelVariant(tag, edit); break;
            case "set_player_representation": SetPlayerRepresentation(tag, edit); break;
            default: throw new NotSupportedException($"unknown op {edit["op"]}");
        }
    }

    // Pin change color N to a single permutation whose lower and upper bounds are the swatch.
    static void SetChangeColor(TagFile tag, Dictionary<string, object> edit)
    {
        int index = Convert.ToInt32(edit["index"]);
        float[] rgb = ((object[])edit["color"]).Select(Convert.ToSingle).ToArray();
        var changeColors = Find<TagFieldBlock>(tag.Fields, Fields["change_colors"]);
        while (changeColors.Elements.Count <= index) changeColors.AddElement();
        var perms = Find<TagFieldBlock>(changeColors.Elements[index].Fields, Fields["change_color_permutations"]);
        perms.RemoveAllElements();
        var perm = perms.AddElement();
        SetData(Find<TagField>(perm.Fields, Fields["permutation_weight"]), 1.0f);
        SetData(Find<TagField>(perm.Fields, Fields["color_lower_bound"]), rgb);
        SetData(Find<TagField>(perm.Fields, Fields["color_upper_bound"]), rgb);
    }

    // Create or replace a named model variant mapping regions to permutations.
    static void SetModelVariant(TagFile tag, Dictionary<string, object> edit)
    {
        string name = (string)edit["variant"];
        var regions = (Dictionary<string, object>)edit["regions"];
        var variants = Find<TagFieldBlock>(tag.Fields, Fields["model_variants"]);
        TagElement variant = null;
        foreach (TagElement v in variants.Elements)
            if (string.Equals(GetData(Find<TagField>(v.Fields, Fields["variant_name"])) as string, name, StringComparison.OrdinalIgnoreCase))
                variant = v;
        if (variant == null)
        {
            variant = variants.AddElement();
            SetData(Find<TagField>(variant.Fields, Fields["variant_name"]), name);
        }
        var regionBlock = Find<TagFieldBlock>(variant.Fields, Fields["variant_regions"]);
        regionBlock.RemoveAllElements();
        foreach (var kv in regions)
        {
            var region = regionBlock.AddElement();
            SetData(Find<TagField>(region.Fields, Fields["region_name"]), kv.Key);
            var perms = Find<TagFieldBlock>(region.Fields, Fields["region_permutations"]);
            var perm = perms.AddElement();
            SetData(Find<TagField>(perm.Fields, Fields["permutation_name"]), (string)kv.Value);
        }
    }

    // Point every campaign player representation at the customizable unit + variant.
    static void SetPlayerRepresentation(TagFile tag, Dictionary<string, object> edit)
    {
        var reps = Find<TagFieldBlock>(tag.Fields, Fields["player_representation"]);
        if (reps.Elements.Count == 0) throw new InvalidDataException("globals has no player representation entries");
        string unit = (string)edit["third_person_unit"];
        foreach (TagElement rep in reps.Elements)
        {
            var unitField = Find<TagFieldReference>(rep.Fields, Fields["third_person_unit"]);
            unitField.Path = ToTagPath(unit);
            SetData(Find<TagField>(rep.Fields, Fields["third_person_variant"]), (string)edit["third_person_variant"]);
        }
    }

    // --- field helpers -------------------------------------------------------

    static T Find<T>(IEnumerable<TagField> fields, string name) where T : TagField
    {
        var hit = Search(fields, name).OfType<T>().FirstOrDefault();
        return hit ?? throw new MissingFieldException($"field '{name}' ({typeof(T).Name}) not found; fix campaign.fields in the catalog");
    }

    // Breadth-first so the shallowest match wins (e.g. object "change colors" before nested ones).
    static IEnumerable<TagField> Search(IEnumerable<TagField> fields, string name)
    {
        var queue = new Queue<TagField>(fields);
        while (queue.Count > 0)
        {
            var f = queue.Dequeue();
            if (Matches(f, name)) yield return f;
            if (f is TagFieldStruct st)
                foreach (TagElement el in st.Elements)
                    foreach (var child in el.Fields) queue.Enqueue(child);
        }
    }

    static bool Matches(TagField f, string name) =>
        string.Equals(f.FieldName?.Trim(), name, StringComparison.OrdinalIgnoreCase)
        || string.Equals(f.DisplayName?.Trim(), name, StringComparison.OrdinalIgnoreCase);

    static object GetData(TagField f) => f.GetType().GetProperty("Data")?.GetValue(f);

    static void SetData(TagField f, object value)
    {
        if (DryRun) { Console.WriteLine($"        {f.FieldName} = {Format(value)}"); return; }
        var prop = f.GetType().GetProperty("Data") ?? throw new NotSupportedException($"{f.FieldName}: no Data property on {f.GetType().Name}");
        var target = prop.PropertyType;
        if (target.IsArray && value is float[] arr)
        {
            var dst = Array.CreateInstance(target.GetElementType(), arr.Length);
            for (int i = 0; i < arr.Length; i++) dst.SetValue(Convert.ChangeType(arr[i], target.GetElementType()), i);
            prop.SetValue(f, dst);
        }
        else
        {
            prop.SetValue(f, target == typeof(string) ? value.ToString() : Convert.ChangeType(value, target));
        }
    }

    static string Format(object v) => v is IEnumerable e && !(v is string) ? "[" + string.Join(", ", e.Cast<object>()) + "]" : v.ToString();
}
