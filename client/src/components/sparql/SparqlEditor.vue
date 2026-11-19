<script setup lang="ts">
/**
 * The SPARQL editor (CodeMirror 6, search design §2.2.4).
 *
 * - SPARQL 1.1 from a Lezer grammar (TASKS 10.4): highlighting, folding of
 *   { … } blocks, indentation, and syntax errors marked as you type; themed from
 *   the FAIR tokens, so it follows light and dark mode.
 * - Completion that knows where the cursor is (src/sparql/lang/context): prefixes
 *   after PREFIX, properties where a predicate goes, classes after `a`, functions
 *   and variables in expressions, the clauses that may follow WHERE { … }. Terms
 *   come from the data (classes and properties with labels and counts, from
 *   /index/sparql/schema); picking `dcat:…` adds the PREFIX line when missing.
 * - Inline validation: the parse tree at once, then the server's gate, debounced.
 * - Ctrl/Cmd+Enter runs the query.
 */
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  placeholder as placeholderExt,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  HighlightStyle,
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import {
  autocompletion,
  closeBrackets,
  completionKeymap,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import { tags } from "@lezer/highlight";
import { sparqlApi, type SparqlSchema } from "@/sparql/api";
import { sparqlSupport, syntaxErrors } from "@/sparql/lang";
import { FUNCTIONS, KEYWORDS, place, variables } from "@/sparql/lang/context";
import { KNOWN_PREFIXES } from "@/sparql/results";

const props = defineProps<{
  modelValue: string;
  schema: SparqlSchema | null;
  label: string;
  placeholder?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string]; run: [] }>();

const host = ref<HTMLDivElement>();
let view: EditorView | null = null;

function prefixes(): Record<string, string> {
  return props.schema?.prefixes ?? KNOWN_PREFIXES;
}

/** Insert `PREFIX p: <…>` at the top when the document does not declare it. */
function prefixChange(doc: string, prefix: string) {
  const namespace = prefixes()[prefix];
  if (!namespace) return null;
  const declared = new RegExp(`PREFIX\\s+${prefix.replace(/[-]/g, "\\-")}:`, "i");
  if (declared.test(doc)) return null;
  return { from: 0, insert: `PREFIX ${prefix}: <${namespace}>\n` };
}

function termOption(curie: string, label: string, count: number, kind: string): Completion {
  const prefix = curie.split(":")[0] ?? "";
  return {
    label: curie,
    detail: `${label} · ${count.toLocaleString()}`,
    type: kind,
    boost: Math.min(99, Math.log10(count + 1) * 10),
    apply: (editor, _completion, from, to) => {
      const extra = prefixChange(editor.state.doc.toString(), prefix);
      editor.dispatch({
        changes: extra ? [{ from, to, insert: curie }, extra] : [{ from, to, insert: curie }],
        selection: { anchor: from + curie.length + (extra ? extra.insert.length : 0) },
      });
    },
  };
}

function prefixOption(prefix: string): Completion {
  return {
    label: `${prefix}:`,
    type: "namespace",
    detail: prefixes()[prefix] ?? "",
    boost: -5,
    apply: (editor, _c, from, to) => {
      const extra = prefixChange(editor.state.doc.toString(), prefix);
      const insert = `${prefix}:`;
      editor.dispatch({
        changes: extra ? [{ from, to, insert }, extra] : [{ from, to, insert }],
        selection: { anchor: from + insert.length + (extra ? extra.insert.length : 0) },
      });
    },
  };
}

function termOptions(text: string, kinds: ("class" | "property")[], boost = 0): Completion[] {
  const options: Completion[] = [];
  for (const kind of kinds) {
    const terms = kind === "class" ? props.schema?.classes : props.schema?.properties;
    for (const term of terms ?? []) {
      if (!term.curie.includes(":") || term.curie.startsWith("http")) continue;
      const name = term.fields?.map((f) => f.label).join(" · ") ?? term.label;
      if (
        term.curie.toLowerCase().startsWith(text) ||
        (!text.includes(":") && name.toLowerCase().includes(text))
      ) {
        const option = termOption(term.curie, name, term.count, kind);
        options.push({ ...option, boost: (option.boost ?? 0) + boost });
      }
    }
  }
  return options;
}

function keywordOptions(words: string[], text: string, boost = -10): Completion[] {
  return words
    .filter((w) => w.toLowerCase().startsWith(text))
    .map((w) => ({ label: w, type: "keyword", boost }));
}

function complete(context: CompletionContext): CompletionResult | null {
  const doc = context.state.doc.toString();
  const variable = context.matchBefore(/[?$][\w]*/);
  if (variable) {
    return {
      from: variable.from,
      options: variables(doc).map((n) => ({ label: n, type: "variable" })),
      validFor: /^[?$]\w*$/,
    };
  }
  const iri = context.matchBefore(/<[^>\s]*/);
  if (iri) {
    return {
      from: iri.from,
      options: (props.schema?.graphs ?? []).map((g) => ({
        label: `<${g.iri}>`,
        detail: g.title,
        type: "constant",
      })),
    };
  }
  const word = context.matchBefore(/[\w-]+:?[\w.-]*/);
  if (!word && !context.explicit) return null;
  const from = word?.from ?? context.pos;
  const text = (word?.text ?? "").toLowerCase();
  if (from === context.pos && !context.explicit) return null;
  const where = place(doc.slice(0, from));
  const vars = variables(doc).map((n) => ({ label: n, type: "variable", boost: 5 }));
  const prefixOptions = text.includes(":")
    ? []
    : Object.keys(prefixes())
        .filter((p) => p.startsWith(text))
        .map(prefixOption);
  let options: Completion[] = [];
  switch (where.kind) {
    case "prefix":
      options = Object.entries(prefixes())
        .filter(([p]) => p.startsWith(text))
        .map(([p, ns]) => ({ label: `${p}: <${ns}>`, type: "namespace", detail: "PREFIX" }));
      break;
    case "start":
    case "modifier":
    case "projection":
      options = [...keywordOptions(KEYWORDS[where.kind], text, 0), ...vars];
      if (where.kind === "projection") {
        options.push(...functionOptions(text).filter((o) => AGGREGATES.has(o.label)));
      }
      break;
    case "expression":
      options = [...functionOptions(text), ...vars, ...keywordOptions(KEYWORDS.expression, text)];
      options.push(...termOptions(text, ["property", "class"], -20), ...prefixOptions);
      break;
    case "subject":
      // The query's variables first: a new triple usually reuses one.
      options = [
        ...vars.map((v) => ({ ...v, boost: 90 })),
        ...keywordOptions(KEYWORDS.subject, text, 0),
        ...termOptions(text, ["class"], -20),
        ...prefixOptions,
      ];
      break;
    case "verb":
      options = [
        ...termOptions(text, ["property"], 10),
        ...keywordOptions(KEYWORDS.verb, text, 99),
        ...vars.map((v) => ({ ...v, boost: -10 })),
        ...prefixOptions,
      ];
      break;
    case "object":
      options = where.classes
        ? [...termOptions(text, ["class"], 10), ...vars, ...prefixOptions]
        : [
            ...vars,
            ...termOptions(text, ["class", "property"], -10),
            ...keywordOptions(KEYWORDS.object, text),
            ...prefixOptions,
          ];
      break;
  }
  return { from, options, validFor: /^[\w-]*:?[\w.-]*$/ };
}

const AGGREGATES = new Set(["COUNT", "SUM", "MIN", "MAX", "AVG", "SAMPLE", "GROUP_CONCAT"]);

function functionOptions(text: string): Completion[] {
  return FUNCTIONS.filter(([name]) => name.toLowerCase().startsWith(text)).map(([name, args]) => ({
    label: name,
    type: "function",
    detail: args,
    apply: name.includes("EXISTS") ? `${name} {  }` : `${name}(`,
  }));
}

const validate = linter(
  async (editor): Promise<Diagnostic[]> => {
    const text = editor.state.doc.toString();
    if (!text.trim()) return [];
    // The parse tree first: a syntax error needs no round trip.
    const local = syntaxErrors(editor.state);
    if (local.length) {
      return local.slice(0, 1).map((e) => ({
        from: e.from,
        to: e.to,
        severity: "error" as const,
        message: "Syntax error here",
      }));
    }
    try {
      const result = await sparqlApi.validate(text);
      if (result.ok) return [];
      const doc = editor.state.doc;
      const lineNo = Math.min(Math.max(result.line ?? 1, 1), doc.lines);
      const line = doc.line(lineNo);
      const from = Math.min(line.from + Math.max((result.column ?? 1) - 1, 0), line.to);
      return [
        {
          from,
          to: Math.min(from + 1, doc.length) || from,
          severity: "error",
          message: result.message ?? "Invalid query",
        },
      ];
    } catch {
      return [];
    }
  },
  { delay: 700 },
);

const highlight = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--tool-accent)", fontWeight: "600" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--fair-success)" },
  { tag: tags.variableName, color: "var(--fair-warning)" },
  { tag: [tags.atom, tags.number, tags.bool], color: "var(--fair-info)" },
  { tag: [tags.comment], color: "var(--fair-text-muted)", fontStyle: "italic" },
  { tag: [tags.url, tags.link, tags.meta], color: "var(--fair-node-darker, var(--fair-text))" },
  { tag: tags.operator, color: "var(--fair-text-strong)" },
  {
    tag: [tags.function(tags.variableName), tags.function(tags.keyword)],
    color: "var(--tool-accent)",
  },
  { tag: tags.attributeName, color: "var(--fair-text-muted)" },
]);

const theme = EditorView.theme({
  "&": {
    backgroundColor: "var(--fair-surface-input)",
    color: "var(--fair-text-strong)",
    fontSize: "13.5px",
    border: "1px solid var(--fair-border)",
    borderRadius: "var(--fair-radius-md)",
  },
  "&.cm-focused": { outline: "2px solid var(--tool-accent)", outlineOffset: "1px" },
  ".cm-content": { fontFamily: "var(--fair-font-mono)", caretColor: "var(--fair-text-strong)" },
  ".cm-gutters": {
    backgroundColor: "var(--fair-code-bg)",
    color: "var(--fair-text-muted)",
    border: "none",
    borderRight: "1px solid var(--fair-border)",
  },
  ".cm-activeLine": { backgroundColor: "var(--fair-highlight)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--fair-highlight)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--tool-accent-tint) !important",
  },
  ".cm-tooltip": {
    backgroundColor: "var(--fair-surface)",
    color: "var(--fair-text)",
    border: "1px solid var(--fair-border)",
    borderRadius: "var(--fair-radius-md)",
  },
  ".cm-tooltip-autocomplete ul li[aria-selected]": {
    backgroundColor: "var(--tool-accent-tint)",
    color: "var(--fair-text-strong)",
  },
  ".cm-completionDetail": { color: "var(--fair-text-muted)", marginLeft: "1em" },
  ".cm-placeholder": { color: "var(--fair-text-light)" },
});

function extensions(): Extension[] {
  return [
    lineNumbers(),
    history(),
    highlightActiveLine(),
    bracketMatching(),
    closeBrackets(),
    foldGutter(),
    indentOnInput(),
    sparqlSupport(),
    syntaxHighlighting(highlight),
    autocompletion({ override: [complete], activateOnTyping: true }),
    validate,
    lintGutter(),
    EditorView.lineWrapping,
    theme,
    placeholderExt(props.placeholder ?? ""),
    EditorView.contentAttributes.of({ "aria-label": props.label }),
    keymap.of([
      {
        key: "Mod-Enter",
        run: () => {
          emit("run");
          return true;
        },
      },
      indentWithTab,
      ...completionKeymap,
      ...foldKeymap,
      ...historyKeymap,
      ...defaultKeymap,
    ]),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) emit("update:modelValue", update.state.doc.toString());
    }),
  ];
}

onMounted(() => {
  if (!host.value) return;
  view = new EditorView({
    parent: host.value,
    state: EditorState.create({ doc: props.modelValue, extensions: extensions() }),
  });
});

watch(
  () => props.modelValue,
  (value) => {
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
    }
  },
);

onBeforeUnmount(() => view?.destroy());

/** Declare every known prefix the query uses but does not declare. */
function addMissingPrefixes(): number {
  if (!view) return 0;
  const doc = view.state.doc.toString();
  // IRIs, strings and comments go first, so only a word character or a hyphen
  // can precede a prefix that is not this one (paths like dcat:x/dct:y included).
  const stripped = doc.replace(
    /<[^<>\s]*>|"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|#[^\n]*/g,
    " ",
  );
  const missing = Object.keys(prefixes()).filter(
    (p) =>
      new RegExp(`(?<![\\w-])${p.replace(/-/g, "\\-")}:`).test(stripped) &&
      !new RegExp(`PREFIX\\s+${p.replace(/-/g, "\\-")}:`, "i").test(doc),
  );
  if (missing.length) {
    const insert = missing.map((p) => `PREFIX ${p}: <${prefixes()[p]}>\n`).join("");
    view.dispatch({ changes: { from: 0, insert } });
  }
  return missing.length;
}

function insertAtCursor(text: string): void {
  if (!view) return;
  const { from, to } = view.state.selection.main;
  view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
  view.focus();
}

defineExpose({ addMissingPrefixes, insertAtCursor, focus: () => view?.focus() });
</script>

<template>
  <div ref="host" class="sparql-editor" />
</template>

<style scoped>
.sparql-editor :deep(.cm-editor) {
  min-height: 240px;
  max-height: 60vh;
}
.sparql-editor :deep(.cm-scroller) {
  overflow: auto;
}
</style>
