#!/bin/bash
# RadarCheck - Fly.io Deployment Script
# Run this script to set up and deploy to Fly.io

set -e  # Exit on error

echo "🚀 RadarCheck Fly.io Deployment Setup"
echo "======================================"
echo ""

# Step 1: Install Fly CLI if not present
if ! command -v flyctl &> /dev/null && ! command -v fly &> /dev/null; then
    echo "📦 Installing Fly CLI..."
    curl -L https://fly.io/install.sh | sh
    
    # Add to PATH for this session
    export FLYCTL_INSTALL="/Users/$USER/.fly"
    export PATH="$FLYCTL_INSTALL/bin:$PATH"
    echo ""
    echo "⚠️  Add this to your ~/.zshrc to make flyctl permanent:"
    echo '   export FLYCTL_INSTALL="$HOME/.fly"'
    echo '   export PATH="$FLYCTL_INSTALL/bin:$PATH"'
    echo ""
fi

# Determine the fly command
FLY_CMD="flyctl"
if command -v fly &> /dev/null; then
    FLY_CMD="fly"
fi

echo "✅ Fly CLI found: $FLY_CMD"
echo ""

# Step 2: Login
echo "📝 Logging in to Fly.io..."
$FLY_CMD auth login
echo ""

# Step 3: Create the app (if not exists)
echo "🏗️  Creating Fly.io app..."
if $FLY_CMD apps list | grep -q "radarcheck"; then
    echo "   App 'radarcheck' already exists"
else
    $FLY_CMD launch --no-deploy --name radarcheck --region ewr --copy-config
fi
echo ""

# Step 4: Create the volume (if not exists)
echo "💾 Creating persistent volume for cache..."
if $FLY_CMD volumes list | grep -q "radar_cache"; then
    echo "   Volume 'radar_cache' already exists"
else
    $FLY_CMD volumes create radar_cache --region ewr --size 1 --yes
fi
echo ""

# Step 5: Generate and set API key
echo "🔐 Setting up API key..."
API_KEY=$(openssl rand -hex 32)
echo "   Generated API key: $API_KEY"
echo "   (Save this key - you'll need it for the iOS app)"
$FLY_CMD secrets set RADARCHECK_API_KEY="$API_KEY"
echo ""

# Step 6: Deploy
echo "🚢 Deploying to Fly.io..."
$FLY_CMD deploy
echo ""

# Step 7: Check status
echo "✅ Deployment complete!"
echo ""
echo "📊 App Status:"
$FLY_CMD status
echo ""
echo "🌍 Your app is available at: https://radarcheck.fly.dev"
echo ""
echo "📝 Useful commands:"
echo "   fly logs          - View live logs"
echo "   fly ssh console   - SSH into the machine"
echo "   fly status        - Check app status"
echo ""
echo "🔑 Your API key (save this!):"
echo "   $API_KEY"
