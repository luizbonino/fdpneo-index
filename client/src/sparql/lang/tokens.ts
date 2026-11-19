/**
 * SPARQL keywords are case-insensitive (`select`, `Select`, `SELECT`), except
 * `a`, which is lower case only. The grammar's `Word` token is specialised here.
 */
import * as terms from "./parser.terms";

const KEYWORDS: Record<string, number> = {
  base: terms.BASE,
  prefix: terms.PREFIX,
  select: terms.SELECT,
  distinct: terms.DISTINCT,
  reduced: terms.REDUCED,
  as: terms.AS,
  construct: terms.CONSTRUCT,
  where: terms.WHERE,
  describe: terms.DESCRIBE,
  ask: terms.ASK,
  from: terms.FROM,
  named: terms.NAMED,
  group: terms.GROUP,
  by: terms.BY,
  having: terms.HAVING,
  order: terms.ORDER,
  asc: terms.ASC,
  desc: terms.DESC,
  limit: terms.LIMIT,
  offset: terms.OFFSET,
  values: terms.VALUES,
  undef: terms.UNDEF,
  union: terms.UNION,
  optional: terms.OPTIONAL,
  minus: terms.MINUS,
  graph: terms.GRAPH,
  service: terms.SERVICE,
  silent: terms.SILENT,
  filter: terms.FILTER,
  bind: terms.BIND,
  exists: terms.EXISTS,
  not: terms.NOT,
  in: terms.IN,
  count: terms.COUNT,
  sum: terms.SUM,
  min: terms.MIN,
  max: terms.MAX,
  avg: terms.AVG,
  sample: terms.SAMPLE,
  group_concat: terms.GROUP_CONCAT,
  separator: terms.SEPARATOR,
  true: terms.TRUE,
  false: terms.FALSE,
};

/** The generated parser passes its stack too; keywords do not depend on it. */
export function keyword(value: string, stack?: unknown): number {
  void stack;
  if (value === "a") return terms.A;
  return KEYWORDS[value.toLowerCase()] ?? -1;
}
