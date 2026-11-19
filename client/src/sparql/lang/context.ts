/**
 * Where the cursor is in a SPARQL query, for completion (TASKS 10.4).
 *
 * The text before the cursor is usually unfinished, so instead of the parse tree
 * (mostly error nodes mid-typing) a small lexer reads it: strings, IRIs and
 * comments are skipped, brackets are tracked, and the last tokens say what may
 * come next.
 */

export type Place =
  | { kind: "prefix" } // after PREFIX: a prefix and its namespace
  | { kind: "start" } // before the query form: PREFIX, BASE, SELECT, ASK …
  | { kind: "projection" } // after SELECT: variables, DISTINCT, *, (expression AS ?x)
  | { kind: "expression" } // inside FILTER( … ), BIND( … ), ( … AS ?x ): functions, variables
  | { kind: "subject" } // a triple's start, or a group keyword (OPTIONAL, FILTER …)
  | { kind: "verb" } // a property
  | { kind: "object"; classes: boolean } // a value; classes first after `a`
  | { kind: "modifier" }; // after WHERE { … }: GROUP BY, ORDER BY, LIMIT …

type Token = { text: string; kind: "word" | "var" | "iri" | "string" | "number" | "punct" };

const TOKEN =
  /\s+|#[^\n]*|<[^<>"{}|^`\\\s]*>?|"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?|[?$][\w]*|\^\^|\|\||&&|!=|<=|>=|[{}()[\];,.*+\-/|^!=<>?]|@[\w-]+|[+-]?\d*\.?\d+(?:[eE][+-]?\d+)?|[^\s{}()[\];,.*/|^!=<>?"'#]+/g;

function tokens(text: string): Token[] {
  const out: Token[] = [];
  for (const match of text.matchAll(TOKEN)) {
    const t = match[0];
    if (/^\s/.test(t) || t.startsWith("#")) continue;
    if (t.startsWith("<") && t.length > 1 && !/^<[=]?$/.test(t)) out.push({ text: t, kind: "iri" });
    else if (/^["']/.test(t)) out.push({ text: t, kind: "string" });
    else if (/^[?$]\w/.test(t)) out.push({ text: t, kind: "var" });
    else if (/^[+-]?\d*\.?\d/.test(t) && !/[a-z_:]/i.test(t)) out.push({ text: t, kind: "number" });
    else if (/^[\wÀ-￿:%\\-]/.test(t)) out.push({ text: t, kind: "word" });
    else out.push({ text: t, kind: "punct" });
  }
  return out;
}

const EXPRESSION_OPENERS = new Set(["filter", "bind", "having", "asc", "desc"]);
const FORMS = new Set(["select", "ask", "construct", "describe"]);
const GROUP_KEYWORDS = new Set([
  "optional",
  "minus",
  "graph",
  "service",
  "silent",
  "union",
  "values",
  "filter",
  "bind",
  "exists",
  "not",
]);

/**
 * The place for completing at the end of ``before`` (the text before the cursor,
 * without the word being typed).
 */
export function place(before: string): Place {
  const all = tokens(before);
  const last = all.at(-1);
  if (last?.kind === "word" && last.text.toLowerCase() === "prefix") return { kind: "prefix" };
  // The open brackets, and for each "(" what opened it.
  const stack: { bracket: string; expression: boolean; at: number }[] = [];
  let sawForm = false;
  for (let i = 0; i < all.length; i++) {
    const token = all[i]!;
    const lower = token.text.toLowerCase();
    if (token.kind === "word" && FORMS.has(lower)) sawForm = true;
    if (token.text === "{" || token.text === "[")
      stack.push({ bracket: token.text, expression: false, at: i });
    else if (token.text === "(") {
      const previous = all[i - 1];
      const inside = stack.at(-1);
      const expression =
        inside?.expression === true ||
        (previous !== undefined &&
          ((previous.kind === "word" &&
            (EXPRESSION_OPENERS.has(previous.text.toLowerCase()) ||
              /^[A-Za-z_]\w*$/.test(previous.text))) ||
            previous.kind === "iri" ||
            (previous.kind === "word" && previous.text.includes(":")))) ||
        isProjection(all, i);
      stack.push({ bracket: "(", expression, at: i });
    } else if (token.text === "}" || token.text === "]" || token.text === ")") stack.pop();
  }
  const open = stack.at(-1);
  if (open?.bracket === "(" && open.expression) return { kind: "expression" };
  const braces = stack.filter((s) => s.bracket === "{");
  if (!braces.length) {
    if (!sawForm) return { kind: "start" };
    // After the outermost group closed: solution modifiers. Before it: the projection.
    const closed = all.some((t) => t.text === "}");
    return closed ? { kind: "modifier" } : { kind: "projection" };
  }
  // Inside a group or a blank node's [ … ]: where in the triple are we?
  const start = (open?.at ?? 0) + 1;
  // Inside [ … ] the subject is the blank node itself.
  const implicit: Token[] = open?.bracket === "[" ? [{ text: "[", kind: "punct" }] : [];
  const terms: Token[] = [...implicit];
  let separator = "";
  for (let i = start; i < all.length; i++) {
    const token = all[i]!;
    const lower = token.text.toLowerCase();
    if (token.kind === "word" && (lower === "filter" || lower === "bind")) {
      // The whole constraint: FILTER( … ), FILTER CONTAINS( … ), BIND( … ).
      let j = i + 1;
      if (all[j] && all[j]!.text !== "(" && all[j + 1]?.text === "(") j += 1;
      if (all[j]?.text === "(") i = closing(all, j);
      terms.length = 0;
      separator = "";
      continue;
    }
    if (token.text === "(" || token.text === "[") {
      // A closed collection, NIL or blank node: one term.
      i = closing(all, i);
      if (separator === ",") separator = "";
      terms.push({ text: token.text, kind: "punct" });
      continue;
    }
    if (token.text === "." || token.text === "{" || token.text === "}") {
      terms.splice(0, terms.length, ...implicit);
      separator = "";
    } else if (token.text === ";" || token.text === ",") {
      separator = token.text;
      if (token.text === ";") terms.splice(1);
    } else if (token.kind === "word" && GROUP_KEYWORDS.has(token.text.toLowerCase())) {
      terms.length = 0;
      separator = "";
    } else if (token.kind !== "punct" || token.text === "*") {
      if (separator === ",") separator = "";
      terms.push(token);
    }
  }
  // Inside a property path: after |, / or ^ another property follows.
  if (last && ["|", "/", "^"].includes(last.text)) return { kind: "verb" };
  if (separator === ";" && terms.length <= 1) return { kind: "verb" };
  if (separator === ",") return { kind: "object", classes: isTypeVerb(terms.at(1)) };
  if (terms.length === 0) return { kind: "subject" };
  if (terms.length === 1) return { kind: "verb" };
  return { kind: "object", classes: isTypeVerb(terms.at(1)) };
}

/** The index of the bracket closing the one at ``index`` (the end when unclosed). */
function closing(all: Token[], index: number): number {
  let depth = 0;
  for (let i = index; i < all.length; i++) {
    const text = all[i]!.text;
    if (text === "(" || text === "[" || text === "{") depth += 1;
    else if (text === ")" || text === "]" || text === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return all.length - 1;
}

function isTypeVerb(token: Token | undefined): boolean {
  return token !== undefined && (token.text === "a" || token.text === "rdf:type");
}

/** A "(" right after SELECT or among its variables: an expression with AS. */
function isProjection(all: Token[], index: number): boolean {
  for (let i = index - 1; i >= 0; i--) {
    const token = all[i]!;
    const lower = token.text.toLowerCase();
    if (lower === "select") return true;
    if (token.kind === "var" || lower === "distinct" || lower === "reduced" || token.text === ")")
      continue;
    return false;
  }
  return false;
}

/** Variables used anywhere in the query, in order of first use. */
export function variables(text: string): string[] {
  return [
    ...new Set(
      tokens(text)
        .filter((t) => t.kind === "var")
        .map((t) => t.text),
    ),
  ];
}

/** SPARQL 1.1 built-in functions and aggregates, with their arguments. */
export const FUNCTIONS: [name: string, args: string][] = [
  ["STR", "(expression)"],
  ["LANG", "(literal)"],
  ["LANGMATCHES", "(language, range)"],
  ["DATATYPE", "(literal)"],
  ["BOUND", "(?variable)"],
  ["IRI", "(string)"],
  ["URI", "(string)"],
  ["BNODE", "(string?)"],
  ["RAND", "()"],
  ["ABS", "(number)"],
  ["CEIL", "(number)"],
  ["FLOOR", "(number)"],
  ["ROUND", "(number)"],
  ["CONCAT", "(string, …)"],
  ["STRLEN", "(string)"],
  ["UCASE", "(string)"],
  ["LCASE", "(string)"],
  ["ENCODE_FOR_URI", "(string)"],
  ["CONTAINS", "(string, part)"],
  ["STRSTARTS", "(string, start)"],
  ["STRENDS", "(string, end)"],
  ["STRBEFORE", "(string, part)"],
  ["STRAFTER", "(string, part)"],
  ["SUBSTR", "(string, start, length?)"],
  ["REPLACE", "(string, pattern, replacement, flags?)"],
  ["REGEX", "(string, pattern, flags?)"],
  ["YEAR", "(dateTime)"],
  ["MONTH", "(dateTime)"],
  ["DAY", "(dateTime)"],
  ["HOURS", "(dateTime)"],
  ["MINUTES", "(dateTime)"],
  ["SECONDS", "(dateTime)"],
  ["TIMEZONE", "(dateTime)"],
  ["TZ", "(dateTime)"],
  ["NOW", "()"],
  ["UUID", "()"],
  ["STRUUID", "()"],
  ["MD5", "(string)"],
  ["SHA1", "(string)"],
  ["SHA256", "(string)"],
  ["SHA384", "(string)"],
  ["SHA512", "(string)"],
  ["COALESCE", "(expression, …)"],
  ["IF", "(condition, then, else)"],
  ["STRLANG", "(string, language)"],
  ["STRDT", "(string, datatype)"],
  ["sameTerm", "(term, term)"],
  ["isIRI", "(term)"],
  ["isURI", "(term)"],
  ["isBLANK", "(term)"],
  ["isLITERAL", "(term)"],
  ["isNUMERIC", "(term)"],
  ["EXISTS", "{ pattern }"],
  ["NOT EXISTS", "{ pattern }"],
  ["COUNT", "(DISTINCT? * | expression)"],
  ["SUM", "(expression)"],
  ["MIN", "(expression)"],
  ["MAX", "(expression)"],
  ["AVG", "(expression)"],
  ["SAMPLE", "(expression)"],
  ["GROUP_CONCAT", '(expression ; SEPARATOR = ", ")'],
];

/** Keywords that fit each place. */
export const KEYWORDS: Record<Place["kind"], string[]> = {
  prefix: [],
  start: ["PREFIX", "BASE", "SELECT", "ASK", "CONSTRUCT", "DESCRIBE"],
  projection: ["DISTINCT", "REDUCED", "*", "WHERE", "FROM", "FROM NAMED", "AS"],
  expression: ["IN", "NOT IN", "AS", "true", "false"],
  subject: ["OPTIONAL", "FILTER", "BIND", "VALUES", "MINUS", "GRAPH", "SERVICE", "UNION", "SELECT"],
  verb: ["a"],
  object: ["true", "false"],
  modifier: ["GROUP BY", "HAVING", "ORDER BY", "ASC", "DESC", "LIMIT", "OFFSET", "VALUES"],
};
