addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request))
  })
  
  const API_ORIGIN = 'https://lyriclocate.kmst.me' // Replace with your backend API domain from Cloudflare Tunnel
  
  async function handleRequest(request) {
    const url = new URL(request.url)
  
    if (url.pathname.startsWith('/api/')) {
      const apiUrl = API_ORIGIN + url.pathname + url.search
      const modifiedRequest = new Request(apiUrl, {
        method: request.method,
        headers: request.headers,
        body: request.body,
        redirect: request.redirect,
      })
      return fetch(modifiedRequest)
    } else {
      return fetch(request)
    }
  }
