import { useState, useEffect } from 'react';
import {
    Box,
    Button,
    CircularProgress,
    Typography,
    List,
    ListItem,
    ListItemText,
    Divider
} from '@mui/material';
import axios from 'axios';

export const HubSpotIntegration = ({ user, org, integrationParams, setIntegrationParams }) => {
    const [isConnected, setIsConnected] = useState(false);
    const [isConnecting, setIsConnecting] = useState(false);
    const [isLoadingData, setIsLoadingData] = useState(false);
    const [hubspotData, setHubspotData] = useState([]);
    const [isClearing, setIsClearing] = useState(false);

    // Function to open OAuth in a new window
    const handleConnectClick = async () => {
        try {
            setIsConnecting(true);
            const formData = new FormData();
            formData.append('user_id', user);
            formData.append('org_id', org);
            const response = await axios.post(`http://localhost:8000/integrations/hubspot/authorize`, formData);
            const authURL = response?.data;

            const newWindow = window.open(authURL, 'HubSpot Authorization', 'width=600, height=600');

            // Polling for the window to close
            const pollTimer = window.setInterval(() => {
                if (newWindow?.closed !== false) { 
                    window.clearInterval(pollTimer);
                    handleWindowClosed();
                }
            }, 200);
        } catch (e) {
            setIsConnecting(false);
            alert(e?.response?.data?.detail);
        }
    };

    // Function to handle logic when the OAuth window closes
    const handleWindowClosed = async () => {
        try {
            const formData = new FormData();
            formData.append('user_id', user);
            formData.append('org_id', org);
            const response = await axios.post(`http://localhost:8000/integrations/hubspot/credentials`, formData);
            const credentials = response.data; 
            if (credentials) {
                setIsConnecting(false);
                setIsConnected(true);
                setIntegrationParams(prev => ({ ...prev, credentials: credentials, type: 'HubSpot' }));
            }
            setIsConnecting(false);
        } catch (e) {
            setIsConnecting(false);
            alert(e?.response?.data?.detail);
        }
    };

    // Function to fetch HubSpot items from backend
    const handleLoadData = async () => {
        if (!integrationParams?.credentials) {
            alert("Please connect to HubSpot first.");
            return;
        }
        setIsLoadingData(true);
        const formData = new FormData();
        formData.append('credentials', JSON.stringify(integrationParams.credentials));
        try {
            const response = await axios.post(
                `http://localhost:8000/integrations/hubspot/get_hubspot_items`,
                formData
            );
            setHubspotData(response.data || []);
        } catch (e) {
            alert("Failed to load HubSpot items: " + (e?.response?.data?.detail || e.message));
        }
        setIsLoadingData(false);
    };

    // Function to clear loaded data
    const handleClearData = async () => {
        setIsClearing(true);
        try {
            setHubspotData([]);
        } catch (e) {
            alert("Failed to clear data: " + e.message);
        }
        setIsClearing(false);
    };

    useEffect(() => {
        setIsConnected(integrationParams?.credentials ? true : false);
    }, [integrationParams]);

    return (
        <>
            <Box sx={{ mt: 2 }}>
                <Typography variant="h6">Parameters</Typography>

                {/* Connect Button */}
                <Box display='flex' alignItems='center' justifyContent='center' sx={{ mt: 2 }}>
                    <Button 
                        variant='contained' 
                        onClick={isConnected ? () => {} : handleConnectClick}
                        color={isConnected ? 'success' : 'primary'}
                        disabled={isConnecting}
                        style={{
                            pointerEvents: isConnected ? 'none' : 'auto',
                            cursor: isConnected ? 'default' : 'pointer',
                            opacity: isConnected ? 1 : undefined
                        }}
                    >
                        {isConnected 
                            ? 'HubSpot Connected' 
                            : isConnecting 
                                ? <CircularProgress size={20} /> 
                                : 'Connect to HubSpot'}
                    </Button>
                </Box>

                {/* Load Data Button (only visible if connected) */}
                {isConnected && (
                    <>
                        <Box display='flex' alignItems='center' justifyContent='center' sx={{ mt: 2 }}>
                            <Button 
                                variant='contained'
                                color='primary'
                                onClick={handleLoadData}
                                disabled={isLoadingData}
                                sx={{ mr: 2 }}
                            >
                                {isLoadingData ? <CircularProgress size={20} /> : 'Load Data'}
                            </Button>
                            <Button 
                                variant='contained'
                                color='secondary'
                                onClick={handleClearData}
                                disabled={isClearing || hubspotData.length === 0}
                            >
                                {isClearing ? <CircularProgress size={20} /> : 'Clear Data'}
                            </Button>
                        </Box>

                        {/* Display loaded data */}
                        {hubspotData.length > 0 && (
                            <Box sx={{ mt: 3 }}>
                                <Typography variant="subtitle1" gutterBottom>
                                    Loaded Data ({hubspotData.length} items)
                                </Typography>
                                <List sx={{ 
                                    maxHeight: 300, 
                                    overflow: 'auto', 
                                    border: '1px solid #ddd', 
                                    borderRadius: 1 
                                }}>
                                    {hubspotData.map((item, index) => (
                                        <div key={index}>
                                            <ListItem>
                                                <ListItemText 
                                                    primary={item.title} 
                                                    secondary={`Type: ${item.type} | ID: ${item.id}`}
                                                />
                                            </ListItem>
                                            {index < hubspotData.length - 1 && <Divider />}
                                        </div>
                                    ))}
                                </List>
                            </Box>
                        )}
                    </>
                )}
            </Box>
        </>
    );
}