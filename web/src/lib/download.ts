/* A file handed to the browser to save: the one place that clicks a download link. */

/** save what `href` points at under `name` */
export function saveFile(href: string, name: string) {
  const a = document.createElement('a')
  a.href = href; a.download = name
  document.body.append(a); a.click(); a.remove()
}

/** save `text` as a file of media type `type` */
export function saveText(text: string, name: string, type: string) {
  const href = URL.createObjectURL(new Blob([text], { type }))
  saveFile(href, name)
  URL.revokeObjectURL(href)
}
