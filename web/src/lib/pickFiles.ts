/* The browser's file picker: the one place that opens it. Its input stays in the page until
   the pick ends (a change, or a cancel where the browser sends one). iPhone Safari lost an
   input that was not in the page while the camera had the screen, so a photo taken from
   Attach never came back to the composer. A new pick removes one left open. */

let open: HTMLInputElement | null = null

/** open the picker; `folder`: a whole folder, each file with its webkitRelativePath;
    `accept`: the media types it offers; `multiple`: more than one file */
export function pickFiles(then: (files: File[]) => void,
                          { folder = false, accept = '', multiple = true } = {}) {
  open?.remove()
  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = multiple
  input.hidden = true
  if (accept) input.accept = accept
  if (folder) input.setAttribute('webkitdirectory', '')
  const done = () => {
    input.remove()
    if (open === input) open = null
  }
  input.addEventListener('change', () => {
    const files = [...(input.files ?? [])]
    done()
    if (files.length) then(files)
  })
  input.addEventListener('cancel', done)
  document.body.append(input)
  open = input
  input.click()
}
