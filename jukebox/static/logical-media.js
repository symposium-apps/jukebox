((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.JukeboxLogicalMedia = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  function groupTracks(allTracks, selectedTracks, keyFor) {
    const all = Array.isArray(allTracks) ? allTracks : [];
    const selected = Array.isArray(selectedTracks) ? selectedTracks : [];
    const groups = new Map();
    for (const track of all) {
      const key = String(keyFor(track));
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(track);
    }
    const keys = [...new Set(selected.map(track => String(keyFor(track))))];
    return keys.map(key => {
      const variants = groups.get(key) || [];
      const byFormat = new Map();
      for (const variant of variants) {
        const format = String(variant.extension || "audio").replace(/^\./, "").toLowerCase();
        const current = byFormat.get(format);
        if (!current || Number(variant.size || 0) > Number(current.size || 0)) byFormat.set(format, variant);
      }
      const formats = [...byFormat.entries()]
        .sort(([left], [right]) => ({ mp3: 0, mp4: 1 }[left] ?? 2) - ({ mp3: 0, mp4: 1 }[right] ?? 2))
        .map(([format, track]) => ({ format, track }));
      const primary = byFormat.get("mp3") || byFormat.get("mp4") || formats[0]?.track || variants[0];
      if (!primary) return null;
      const metadataSource = variants.find(variant => variant.artist || variant.album_cover || variant.album_cover_pixel || variant.album_cover_lcd) || primary;
      return {
        ...primary,
        artist: primary.artist || metadataSource.artist || "",
        album: primary.album || metadataSource.album || "",
        album_cover: primary.album_cover || metadataSource.album_cover || "",
        album_cover_pixel: primary.album_cover_pixel || metadataSource.album_cover_pixel || "",
        album_cover_lcd: primary.album_cover_lcd || metadataSource.album_cover_lcd || "",
        album_cover_lcd_path: primary.album_cover_lcd_path || metadataSource.album_cover_lcd_path || "",
        logical_key: key,
        variant_ids: variants.map(variant => String(variant.id)),
        format_variants: formats.map(item => ({
          format: item.format,
          track_id: String(item.track.id),
          size: Number(item.track.size || 0)
        }))
      };
    }).filter(Boolean);
  }

  return { groupTracks };
});
