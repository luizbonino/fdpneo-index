<script setup lang="ts">
/**
 * The SPARQL editor (CodeMirror 6, search design §2.2.4).
 *
 * - SPARQL highlighting (the legacy stream mode), themed from the FAIR tokens,
 *   so it follows light and dark mode.
 * - Autocomplete from the data: classes and properties with labels and counts
 *   (from /index/sparql/schema), prefixes, keywords, the query's variables and
 *   the FDP graphs. Picking `dcat:…` adds the PREFIX line when it is missing.
 * - Inline validation: the server's gate, debounced, with line and column.
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
  StreamLanguage,
  bracketMatching,
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
import { sparql } from "@codemirror/legacy-modes/mode/sparql";
import { tags } from "@lezer/highlight";
import { sparqlApi, type SparqlSchema } from "@/sparql/api";
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

const KEYWORDS = [
  "SELECT",
  "DISTINCT",
  "WHERE",
  "FILTER",
  "OPTIONAL",
  "GRAPH",
  "UNION",
  "MINUS",
  "VALUES",
  "BIND",
  "AS",
  "GROUP BY",
  "ORDER BY",
  "DESC",
  "ASC",
  "LIMIT",
  "OFFSET",
  "HAVING",
  "COUNT",
  "SAMPLE",
  "GROUP_CONCAT",
  "MIN",
  "MAX",
  "AVG",
  "SUM",
  "CONSTRUCT",
  "DESCRIBE",
  "ASK",
  "PREFIX",
  "NOT EXISTS",
  "EXISTS",
  "CONTAINS",
  "LCASE",
  "UCASE",
  "STR",
  "LANG",
  "LANGMATCHES",
  "REGEX",
  "STRSTARTS",
  "STRENDS",
  "BOUND",
  "COALESCE",
  "IF",
  "IN",
  "DATATYPE",
  "YEAR",
];

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

function complete(context: CompletionContext): CompletionResult | null {
  const variable = context.matchBefore(/[?$][\w]*/);
  if (variable) {
    const names = new Set(context.state.doc.toString().match(/[?$][A-Za-z_]\w*/g) ?? []);
    return {
      from: variable.from,
      options: [...names].map((n) => ({ label: n, type: "variable" })),
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
  if (!word || (word.from === word.to && !context.explicit)) return null;
  const text = word.text.toLowerCase();
  const options: Completion[] = [];
  const terms = [
    ...(props.schema?.classes ?? []).map((t) => ({ ...t, kind: "class" })),
    ...(props.schema?.properties ?? []).map((t) => ({ ...t, kind: "property" })),
  ];
  for (const term of terms) {
    if (!term.curie.includes(":") || term.curie.startsWith("http")) continue;
    if (
      term.curie.toLowerCase().startsWith(text) ||
      (!text.includes(":") && term.label.toLowerCase().includes(text))
    ) {
      options.push(termOption(term.curie, term.label, term.count, term.kind));
    }
  }
  if (!text.includes(":")) {
    for (const prefix of Object.keys(prefixes())) {
      if (prefix.startsWith(text)) {
        options.push({
          label: `${prefix}:`,
          type: "namespace",
          detail: prefixes()[prefix] ?? "",
          apply: (editor, _c, from, to) => {
            const extra = prefixChange(editor.state.doc.toString(), prefix);
            const insert = `${prefix}:`;
            editor.dispatch({
              changes: extra ? [{ from, to, insert }, extra] : [{ from, to, insert }],
              selection: { anchor: from + insert.length + (extra ? extra.insert.length : 0) },
            });
          },
        });
      }
    }
    for (const keyword of KEYWORDS) {
      if (keyword.toLowerCase().startsWith(text)) {
        options.push({ label: keyword, type: "keyword", boost: -10 });
      }
    }
  }
  return { from: word.from, options, validFor: /^[\w-]*:?[\w.-]*$/ };
}

const validate = linter(
  async (editor): Promise<Diagnostic[]> => {
    const text = editor.state.doc.toString();
    if (!text.trim()) return [];
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
    StreamLanguage.define(sparql),
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
