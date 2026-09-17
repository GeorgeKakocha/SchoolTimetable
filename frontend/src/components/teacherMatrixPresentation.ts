/** Format a positive integer as a Roman numeral for Matrix presentation.
 * This never interprets or changes a configured Period ID/name/index. */
export function toRomanNumeral(value: number): string {
  if (!Number.isInteger(value) || value < 1) {
    throw new RangeError("Roman numeral value must be a positive integer.");
  }
  const tokens: ReadonlyArray<readonly [number, string]> = [
    [1000, "M"], [900, "CM"], [500, "D"], [400, "CD"],
    [100, "C"], [90, "XC"], [50, "L"], [40, "XL"],
    [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"],
  ];
  let remainder = value;
  let result = "";
  for (const [amount, token] of tokens) {
    while (remainder >= amount) {
      result += token;
      remainder -= amount;
    }
  }
  return result;
}
