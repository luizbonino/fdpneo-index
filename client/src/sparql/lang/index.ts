/**
 * SPARQL for CodeMirror (TASKS 10.4): the Lezer grammar of SPARQL 1.1 Query
 * (sparql.grammar) with highlighting, folding of { … } and [ … ], indentation,
 * and syntax errors from the parse tree. Completion is in the editor component,
 * guided by ./context.
 */
import {
  LRLanguage,
  LanguageSupport,
  delimitedIndent,
  foldInside,
  foldNodeProp,
  indentNodeProp,
  syntaxTree,
} from "@codemirror/language";
import type { EditorState } from "@codemirror/state";
import { styleTags, tags as t } from "@lezer/highlight";
import { parser } from "./parser";

export const sparqlLanguage = LRLanguage.define({
  name: "sparql",
  parser: parser.configure({
    props: [
      styleTags({
        "BASE PREFIX SELECT DISTINCT REDUCED AS CONSTRUCT WHERE DESCRIBE ASK FROM NAMED GROUP BY HAVING ORDER ASC DESC LIMIT OFFSET VALUES UNDEF UNION OPTIONAL MINUS GRAPH SERVICE SILENT FILTER BIND EXISTS NOT IN SEPARATOR A":
          t.keyword,
        "COUNT SUM MIN MAX AVG SAMPLE GROUP_CONCAT": t.function(t.keyword),
        "TRUE FALSE": t.bool,
        Var: t.variableName,
        String: t.string,
        LangTag: t.attributeName,
        Number: t.number,
        IriRef: t.url,
        PrefixedName: t.link,
        BlankNodeLabel: t.special(t.variableName),
        Comment: t.lineComment,
        FunctionName: t.function(t.variableName),
        "CompareOp LogicOp ArithOp PathMod Star": t.operator,
        '"!" "^" "^^" "|" "/"': t.operator,
        "( )": t.paren,
        "{ }": t.brace,
        "[ ]": t.squareBracket,
        ". , ;": t.separator,
      }),
      foldNodeProp.add({
        GroupGraphPattern: foldInside,
        ConstructTemplate: foldInside,
        BlankNodePropertyList: foldInside,
        InlineDataOneVar: foldInside,
        InlineDataFull: foldInside,
      }),
      indentNodeProp.add({
        GroupGraphPattern: delimitedIndent({ closing: "}" }),
        ConstructTemplate: delimitedIndent({ closing: "}" }),
        BlankNodePropertyList: delimitedIndent({ closing: "]" }),
      }),
    ],
  }),
  languageData: {
    commentTokens: { line: "#" },
    closeBrackets: { brackets: ["(", "[", "{", "'", '"'] },
    indentOnInput: /^\s*[}\]]$/,
  },
});

export function sparqlSupport(): LanguageSupport {
  return new LanguageSupport(sparqlLanguage);
}

/** Syntax errors in the parse tree, for an immediate marker before the server's check. */
export function syntaxErrors(state: EditorState): { from: number; to: number }[] {
  const found: { from: number; to: number }[] = [];
  syntaxTree(state).iterate({
    enter(node) {
      if (!node.type.isError) return;
      const from = node.from;
      found.push({ from, to: Math.min(Math.max(node.to, from + 1), state.doc.length) });
    },
  });
  return found;
}
