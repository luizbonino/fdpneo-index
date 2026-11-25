/**
 * Continents for the Statistics maps: each country (ISO 3166-1 alpha-2) by the
 * UN M49 regions, with the Americas split at Panama (Central America and the
 * Caribbean with North America). Cyprus is placed in Europe, where M49 has it
 * in Western Asia, as the EU does; Russia in Europe, as M49 does.
 */
export const CONTINENTS = ["AF", "AS", "EU", "NA", "SA", "OC", "AN"] as const;
export type Continent = (typeof CONTINENTS)[number];

const MEMBERS: Record<Continent, string> = {
  AF: "AO BF BI BJ BW CD CF CG CI CM CV DJ DZ EG EH ER ET GA GH GM GN GQ GW IO KE KM LR LS LY MA MG ML MR MU MW MZ NA NE NG RE RW SC SD SH SL SN SO SS ST SZ TD TF TG TN TZ UG YT ZA ZM ZW",
  AS: "AE AF AM AZ BD BH BN BT CN GE HK ID IL IN IQ IR JO JP KG KH KP KR KW KZ LA LB LK MM MN MO MV MY NP OM PH PK PS QA SA SG SY TH TJ TL TM TR TW UZ VN YE",
  EU: "AD AL AT AX BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GG GI GR HR HU IE IM IS IT JE LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS RU SE SI SJ SK SM UA VA XK",
  NA: "AG AI AW BB BL BM BQ BS BZ CA CR CU CW DM DO GD GL GP GT HN HT JM KN KY LC MF MQ MS MX NI PA PM PR SV SX TC TT UM US VC VG VI",
  SA: "AR BO BR BV CL CO EC FK GF GS GY PE PY SR UY VE",
  OC: "AS AU CC CK CX FJ FM GU HM KI MH MP NC NF NR NU NZ PF PG PN PW SB TK TO TV VU WF WS",
  AN: "AQ",
};

export const CONTINENT_OF: Record<string, Continent> = Object.fromEntries(
  CONTINENTS.flatMap((continent) => MEMBERS[continent].split(" ").map((code) => [code, continent])),
);

/**
 * The area each continent's view shows: west, south, east, north in degrees. A
 * box, not the countries' extent, so Russia's east or French Guiana does not
 * stretch Europe's view.
 */
export const CONTINENT_BOX: Record<Continent, [number, number, number, number]> = {
  AF: [-20, -36, 55, 38],
  AS: [25, -12, 150, 58],
  EU: [-25, 34, 45, 71],
  NA: [-170, 5, -50, 75],
  SA: [-85, -56, -33, 13],
  OC: [110, -48, 180, -5],
  AN: [-180, -90, 180, -60],
};
