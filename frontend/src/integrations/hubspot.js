// frontend/src/integrations/hubspot.js
export default {
  id: "hubspot",
  title: "HubSpot",
  description: "Connect to HubSpot and load contacts, companies, and deals",
  // starts OAuth - hits backend which redirects to HubSpot
  authorize: () => {
    // open the backend authorize endpoint in a new window/tab
    const url = "/integrations/hubspot/authorize"; // adjust if your backend mount differs
    // open in new tab (so HubSpot callbacks back to backend redirect)
    window.open(url, "_blank", "noopener,noreferrer");
  },

  // fetchItems is called by UI where integrations load items
  fetchItems: async () => {
    // This endpoint should be implemented server-side to return the items
    const res = await fetch("/integrations/hubspot/items");
    if (!res.ok) {
      const txt = await res.text();
      throw new Error("Failed loading HubSpot items: " + txt);
    }
    return await res.json(); // expects array of items
  },
};
