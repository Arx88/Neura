'use client';

import { useSubscription } from '@/hooks/react-query/subscriptions/use-subscriptions';
import { useState, useEffect, useMemo } from 'react';
import { isLocalMode } from '@/lib/config';
// import { useAvailableModels } from '@/hooks/react-query/subscriptions/use-model'; // Replaced
import { useAllLlmModels } from '@/hooks/react-query/subscriptions/use-model'; // Added
import { AllLlmModelsResponse, ApiProviderModels, ApiModelInfo } from '@/lib/api'; // Added

export const STORAGE_KEY_MODEL = 'suna-preferred-model';
export const STORAGE_KEY_CUSTOM_MODELS = 'customModels';
export const DEFAULT_FREE_MODEL_ID = 'deepseek';
export const DEFAULT_PREMIUM_MODEL_ID = 'claude-sonnet-4';

export type SubscriptionStatus = 'no_subscription' | 'active';

export interface ModelOption {
  id: string;
  label: string;
  requiresSubscription: boolean;
  description?: string;
  top?: boolean;
  isCustom?: boolean;
  priority?: number;
  provider?: string; // Added
  configured?: boolean; // Added
}

export interface CustomModel {
  id: string;
  label: string;
}

// SINGLE SOURCE OF TRUTH for all model data
export const MODELS = {
  // Premium high-priority models
  'claude-sonnet-4': { 
    tier: 'premium',
    priority: 100, 
    recommended: true,
    lowQuality: false,
    description: 'Claude Sonnet 4 - Anthropic\'s latest and most advanced AI assistant'
  },
  'claude-sonnet-3.7': { 
    tier: 'premium', 
    priority: 95, 
    recommended: true,
    lowQuality: false,
    description: 'Claude 3.7 - Anthropic\'s most powerful AI assistant'
  },
  'claude-sonnet-3.7-reasoning': { 
    tier: 'premium', 
    priority: 95, 
    recommended: true,
    lowQuality: false,
    description: 'Claude 3.7 with enhanced reasoning capabilities'
  },
  'gpt-4.1': { 
    tier: 'premium', 
    priority: 95,
    recommended: false,
    lowQuality: false,
    description: 'GPT-4.1 - OpenAI\'s most advanced model with enhanced reasoning'
  },
  'gemini-2.5-pro-preview': { 
    tier: 'premium', 
    priority: 95,
    recommended: true,
    lowQuality: false,
    description: 'Gemini Pro 2.5 - Google\'s latest powerful model with strong reasoning'
  },
  'gemini-2.5-pro': { 
    tier: 'premium', 
    priority: 95,
    recommended: true,
    lowQuality: false,
    description: 'Gemini Pro 2.5 - Google\'s latest advanced model'
  },
  'claude-3.5': { 
    tier: 'premium', 
    priority: 90,
    recommended: true,
    lowQuality: false,
    description: 'Claude 3.5 - Anthropic\'s balanced model with solid capabilities'
  },
  'gemini-2.5': { 
    tier: 'premium', 
    priority: 90,
    recommended: true,
    lowQuality: false,
    description: 'Gemini 2.5 - Google\'s powerful versatile model'
  },
  'gemini-flash-2.5:thinking': { 
    tier: 'premium', 
    priority: 90,
    recommended: true,
    lowQuality: false,
    description: 'Gemini Flash 2.5 - Google\'s fast, responsive AI model'
  },
  'gpt-4o': { 
    tier: 'premium', 
    priority: 85,
    recommended: false,
    lowQuality: false,
    description: 'GPT-4o - Optimized for speed, reliability, and cost-effectiveness'
  },
  'gpt-4-turbo': { 
    tier: 'premium', 
    priority: 85,
    recommended: false,
    lowQuality: false,
    description: 'GPT-4 Turbo - OpenAI\'s powerful model with a great balance of performance and cost'
  },
  'gpt-4': { 
    tier: 'premium', 
    priority: 80,
    recommended: false,
    lowQuality: false,
    description: 'GPT-4 - OpenAI\'s highly capable model with advanced reasoning'
  },
  'deepseek-chat-v3-0324': { 
    tier: 'premium', 
    priority: 75,
    recommended: true,
    lowQuality: false,
    description: 'DeepSeek Chat - Advanced AI assistant with strong reasoning'
  },
  
  // Free tier models
  'deepseek-r1': { 
    tier: 'free', 
    priority: 60,
    recommended: false,
    lowQuality: false,
    description: 'DeepSeek R1 - Advanced model with enhanced reasoning and coding capabilities'
  },
  'deepseek': { 
    tier: 'free', 
    priority: 50,
    recommended: false,
    lowQuality: true,
    description: 'DeepSeek - Free tier model with good general capabilities'
  },
  'gemini-flash-2.5': { 
    tier: 'free', 
    priority: 50,
    recommended: false,
    lowQuality: true,
    description: 'Gemini Flash - Google\'s faster, more efficient model'
  },
  'grok-3-mini': { 
    tier: 'free', 
    priority: 45,
    recommended: false,
    lowQuality: true,
    description: 'Grok-3 Mini - Smaller, faster version of Grok-3 for simpler tasks'
  },
  'qwen3': { 
    tier: 'free', 
    priority: 40,
    recommended: false,
    lowQuality: true,
    description: 'Qwen3 - Alibaba\'s powerful multilingual language model'
  },
};

// Model tier definitions
export const MODEL_TIERS = {
  premium: {
    requiresSubscription: true,
    baseDescription: 'Advanced model with superior capabilities'
  },
  free: {
    requiresSubscription: false,
    baseDescription: 'Available to all users'
  },
  custom: {
    requiresSubscription: false,
    baseDescription: 'User-defined model'
  }
};

// Helper to check if a user can access a model based on subscription status
// export const canAccessModel = ( // Renamed and replaced
//   subscriptionStatus: SubscriptionStatus,
//   requiresSubscription: boolean,
// ): boolean => {
//   if (isLocalMode()) {
//     return true;
//   }
//   return subscriptionStatus === 'active' || !requiresSubscription;
// };

// Helper to format a model name for display
export const formatModelName = (name: string): string => {
  return name
    .split('-')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
};

// Add openrouter/ prefix to custom models
export const getPrefixedModelId = (modelId: string, isCustom: boolean): string => {
  if (isCustom && !modelId.startsWith('openrouter/')) {
    return `openrouter/${modelId}`;
  }
  return modelId;
};

// Helper to get custom models from localStorage
export const getCustomModels = (): CustomModel[] => {
  if (!isLocalMode() || typeof window === 'undefined') return [];
  
  try {
    const storedModels = localStorage.getItem(STORAGE_KEY_CUSTOM_MODELS);
    if (!storedModels) return [];
    
    const parsedModels = JSON.parse(storedModels);
    if (!Array.isArray(parsedModels)) return [];
    
    return parsedModels
      .filter((model: any) => 
        model && typeof model === 'object' && 
        typeof model.id === 'string' && 
        typeof model.label === 'string');
  } catch (e) {
    console.error('Error parsing custom models:', e);
    return [];
  }
};

// Helper to save model preference to localStorage safely
const saveModelPreference = (modelId: string): void => {
  try {
    localStorage.setItem(STORAGE_KEY_MODEL, modelId);
  } catch (error) {
    console.warn('Failed to save model preference to localStorage:', error);
  }
};

export const useModelSelection = () => {
  const [selectedModel, setSelectedModel] = useState(DEFAULT_FREE_MODEL_ID);
  const [customModels, setCustomModels] = useState<CustomModel[]>([]);
  
  const { data: subscriptionData } = useSubscription();
  // const { data: modelsData, isLoading: isLoadingModels } = useAvailableModels({ // Replaced
  //   refetchOnMount: false,
  // });
  const { data: allModelsData, isLoading: isLoadingAllModels } = useAllLlmModels(); // Added
  
  const subscriptionStatus: SubscriptionStatus = subscriptionData?.status === 'active' 
    ? 'active' 
    : 'no_subscription';

  // Function to refresh custom models from localStorage
  const refreshCustomModels = () => {
    if (isLocalMode() && typeof window !== 'undefined') {
      const freshCustomModels = getCustomModels();
      setCustomModels(freshCustomModels);
    }
  };

  // Load custom models from localStorage
  useEffect(() => {
    refreshCustomModels();
  }, []);

  // Generate model options list with consistent structure
  const MODEL_OPTIONS = useMemo(() => {
    const modelOptionsList: ModelOption[] = [];

    if (isLoadingAllModels) {
      // Return a minimal or empty list while loading, or a default loading state.
      // For now, let's use the previous approach of default models if API is loading.
      // This part can be refined later if a specific loading UI for models is needed.
      return [
        { 
          id: DEFAULT_FREE_MODEL_ID, 
          label: 'DeepSeek (Loading...)', 
          requiresSubscription: false,
          description: MODELS[DEFAULT_FREE_MODEL_ID]?.description || MODEL_TIERS.free.baseDescription,
          priority: MODELS[DEFAULT_FREE_MODEL_ID]?.priority || 50,
          provider: 'default',
          configured: true,
        },
        { 
          id: DEFAULT_PREMIUM_MODEL_ID, 
          label: 'Claude Sonnet 4 (Loading...)', 
          requiresSubscription: true, 
          description: MODELS[DEFAULT_PREMIUM_MODEL_ID]?.description || MODEL_TIERS.premium.baseDescription,
          priority: MODELS[DEFAULT_PREMIUM_MODEL_ID]?.priority || 100,
          provider: 'default',
          configured: true,
        },
      ];
    }

    if (allModelsData) {
      // Define a preferred provider order for later sorting
      const providerOrder = ["ollama", "openai", "anthropic", "openrouter", "groq", "bedrock", "custom"];

      Object.entries(allModelsData).forEach(([providerKey, providerDetails]) => {
        const currentProvider = providerDetails as ApiProviderModels | undefined; // Type assertion
        if (currentProvider && currentProvider.models) {
          currentProvider.models.forEach((apiModel: ApiModelInfo) => {
            const modelMeta = MODELS[apiModel.id] || MODELS[apiModel.id.replace(/^openrouter\//, '')] || {}; // Check with and without openrouter prefix for MODELS match
            
            let reqSub = !['ollama', 'custom'].includes(providerKey); // Default based on provider type
            // Override with specific tier info from MODELS constant if available
            if (modelMeta.tier === 'free') reqSub = false;
            if (modelMeta.tier === 'premium') reqSub = true;

            modelOptionsList.push({
              id: apiModel.id, // ID from backend is king
              label: apiModel.name, // Name from backend is king
              requiresSubscription: reqSub,
              description: modelMeta.description || `Model from ${providerKey}`,
              top: (modelMeta.priority || 0) >= 90,
              priority: modelMeta.priority || 50, // Default priority
              lowQuality: modelMeta.lowQuality || false,
              recommended: modelMeta.recommended || false,
              provider: providerKey,
              configured: currentProvider.configured,
              isCustom: false, // Will be set true for actual custom models later
            });
          });
        }
      });
    } else if (!isLoadingAllModels) {
      // Handle case where data is null and not loading (e.g., API error)
      // Fallback to a minimal set of hardcoded models or show an error state.
      // For now, using a similar default as loading state but without "(Loading...)"
       modelOptionsList.push(
        { 
          id: DEFAULT_FREE_MODEL_ID, 
          label: 'DeepSeek', 
          requiresSubscription: false,
          description: MODELS[DEFAULT_FREE_MODEL_ID]?.description || MODEL_TIERS.free.baseDescription,
          priority: MODELS[DEFAULT_FREE_MODEL_ID]?.priority || 50,
          provider: 'fallback',
          configured: true,
        },
        { 
          id: DEFAULT_PREMIUM_MODEL_ID, 
          label: 'Claude Sonnet 4', 
          requiresSubscription: true, 
          description: MODELS[DEFAULT_PREMIUM_MODEL_ID]?.description || MODEL_TIERS.premium.baseDescription,
          priority: MODELS[DEFAULT_PREMIUM_MODEL_ID]?.priority || 100,
          provider: 'fallback',
          configured: true,
        }
      );
    }
    
    // Add custom models if in local mode
    if (isLocalMode() && customModels.length > 0) {
      const customModelOptions = customModels.map(customModel => {
        const modelMeta = MODELS[customModel.id] || MODELS[customModel.id.replace(/^openrouter\//, '')] || {}; // Match with or without openrouter/
        return {
          id: customModel.id, // Use the ID from custom model storage
          label: customModel.label || formatModelName(customModel.id), // Use label or format ID
          requiresSubscription: false, // Custom models are assumed free
          description: modelMeta.description || MODEL_TIERS.custom.baseDescription,
          top: false,
          isCustom: true,
          priority: modelMeta.priority || 30, // Default priority for custom
          lowQuality: modelMeta.lowQuality || false,
          recommended: modelMeta.recommended || false,
          provider: 'custom', // Specific provider key for custom models
          configured: true, // Custom models are always considered configured
        };
      });
      modelOptionsList.push(...customModelOptions);
    }

    // Define provider sort order
    const providerSortOrder: { [key: string]: number } = {
      ollama: 1,
      openai: 2,
      anthropic: 3,
      openrouter: 4, // Keep OpenRouter models grouped
      groq: 5,
      bedrock: 6,
      custom: 7, // Custom models last or as per preference
      default: 8, // Loading state models
      fallback: 9, // Fallback error state models
    };
    
    // Sort models:
    // 1. Primary sort: `configured` (true first).
    // 2. Secondary sort: `provider` (custom order).
    // 3. Tertiary sort: `priority` (descending).
    // 4. Quaternary sort: `label` (alphabetical).
    return modelOptionsList.sort((a, b) => {
      // Sort by configured (true first)
      if ((a.configured ?? false) !== (b.configured ?? false)) {
        return (a.configured ?? false) ? -1 : 1;
      }
      // Sort by provider order
      const aProviderOrder = providerSortOrder[a.provider || 'custom'] || 99;
      const bProviderOrder = providerSortOrder[b.provider || 'custom'] || 99;
      if (aProviderOrder !== bProviderOrder) {
        return aProviderOrder - bProviderOrder;
      }
      // Sort by priority (descending)
      if ((a.priority || 0) !== (b.priority || 0)) {
        return (b.priority || 0) - (a.priority || 0);
      }
      // Sort by label (alphabetical)
      return (a.label || '').localeCompare(b.label || '');
    });

  }, [allModelsData, isLoadingAllModels, customModels]);

  // Get filtered list of models the user can access
  // This will be updated after MODEL_OPTIONS is fully refactored with sorting and custom models.
  const canAccessModelCheck = (modelId: string, currentSubscriptionStatus: SubscriptionStatus, currentModelOptions: ModelOption[]): boolean => {
    if (isLocalMode()) return true; // Local mode bypasses normal checks
    
    const model = currentModelOptions.find(m => m.id === modelId);
    if (!model) return false;
    if (!model.configured) return false; // Provider must be configured
    
    // Custom and Ollama models are accessible if their provider is configured (Ollama checked by API, custom is always true)
    if (model.provider === 'custom' || model.provider === 'ollama') return true; 

    // For other providers, check subscription status vs model's requirement
    return currentSubscriptionStatus === 'active' || !model.requiresSubscription;
  };
  
  const availableModels = useMemo(() => {
    return MODEL_OPTIONS.filter(model => canAccessModelCheck(model.id, subscriptionStatus, MODEL_OPTIONS));
  }, [MODEL_OPTIONS, subscriptionStatus]);

  // Initialize selected model from localStorage or defaults
  useEffect(() => {
    if (typeof window === 'undefined' || MODEL_OPTIONS.length === 0) return;

    try {
      const savedModelId = localStorage.getItem(STORAGE_KEY_MODEL);
      let newSelectedModelId = '';

      // Check if saved model is valid and accessible
      if (savedModelId && canAccessModelCheck(savedModelId, subscriptionStatus, MODEL_OPTIONS)) {
        newSelectedModelId = savedModelId;
      } else {
        // Find a new default model based on priority
        // 1. Try configured Ollama model
        let defaultModel = MODEL_OPTIONS.find(m => m.provider === 'ollama' && m.configured);
        if (defaultModel) {
          newSelectedModelId = defaultModel.id;
        } else {
          // 2. If subscription active, try configured, recommended, premium model
          if (subscriptionStatus === 'active') {
            defaultModel = MODEL_OPTIONS.find(m => 
              m.configured && 
              m.requiresSubscription && 
              m.recommended &&
              m.provider !== 'ollama' // exclude ollama as it was checked
            );
            if (defaultModel) newSelectedModelId = defaultModel.id;
          }
          
          // 3. If no model yet, try configured, free, non-Ollama model
          if (!newSelectedModelId) {
            defaultModel = MODEL_OPTIONS.find(m => 
              m.configured && 
              !m.requiresSubscription && 
              m.provider !== 'ollama'
            );
            if (defaultModel) newSelectedModelId = defaultModel.id;
          }

          // 4. Fallback to DEFAULT_FREE_MODEL_ID if accessible, then first available
          if (!newSelectedModelId) {
            if (canAccessModelCheck(DEFAULT_FREE_MODEL_ID, subscriptionStatus, MODEL_OPTIONS)) {
              newSelectedModelId = DEFAULT_FREE_MODEL_ID;
            } else {
              const firstAvailable = MODEL_OPTIONS.find(m => canAccessModelCheck(m.id, subscriptionStatus, MODEL_OPTIONS));
              if (firstAvailable) {
                newSelectedModelId = firstAvailable.id;
              } else {
                // This case should ideally not happen if MODEL_OPTIONS has items
                console.warn("No accessible models found. Defaulting to the first model in the list or empty.");
                newSelectedModelId = MODEL_OPTIONS.length > 0 ? MODEL_OPTIONS[0].id : ''; 
              }
            }
          }
        }
      }
      
      if (newSelectedModelId) {
        setSelectedModel(newSelectedModelId);
        saveModelPreference(newSelectedModelId);
      } else if (MODEL_OPTIONS.length > 0) {
        // If after all checks, newSelectedModelId is empty but options are available,
        // default to the first one (could be a non-configured one if nothing else is available)
        // This is a safeguard.
        const fallbackDefault = MODEL_OPTIONS[0].id;
        setSelectedModel(fallbackDefault);
        saveModelPreference(fallbackDefault);
        console.warn(`Default model selection fell back to the first model in the list: ${fallbackDefault}`);
      } else {
        // No models available at all (e.g. API error and no defaults loaded)
        setSelectedModel(''); // Set to empty or a specific "no model available" ID
        console.warn('No models available to select.');
      }

    } catch (error) {
      console.warn('Failed to load model preferences from localStorage or initialize default:', error);
      // Fallback to a hardcoded default if everything else fails
      const ultimateFallback = MODEL_OPTIONS.find(m => m.id === DEFAULT_FREE_MODEL_ID && m.configured) 
                               ? DEFAULT_FREE_MODEL_ID 
                               : (MODEL_OPTIONS.length > 0 ? MODEL_OPTIONS[0].id : '');
      setSelectedModel(ultimateFallback);
      if (ultimateFallback) saveModelPreference(ultimateFallback);
    }
  }, [subscriptionStatus, MODEL_OPTIONS, isLoadingAllModels]); // Depend on isLoadingAllModels to re-run when models finish loading


  // Handle model selection change
  const handleModelChange = (modelId: string) => {
    console.log('handleModelChange', modelId);
    
    // Refresh custom models from localStorage to ensure we have the latest
    if (isLocalMode()) {
      refreshCustomModels();
    }
    
    // First check if it's a custom model in local mode
    const isCustomModel = isLocalMode() && customModels.some(model => model.id === modelId);
    
    // Then check if it's in standard MODEL_OPTIONS
    const modelOption = MODEL_OPTIONS.find(option => option.id === modelId);
    
    // Check if model exists in either custom models or standard options
    if (!modelOption && !isCustomModel) {
      console.warn('Model not found in options:', modelId, MODEL_OPTIONS, isCustomModel, customModels);
      
      // Reset to default model when the selected model is not found
      const defaultModel = isLocalMode() ? DEFAULT_PREMIUM_MODEL_ID : DEFAULT_FREE_MODEL_ID;
      setSelectedModel(defaultModel);
      saveModelPreference(defaultModel);
      return;
    }

    // Check access permissions (except for custom models in local mode)
    if (!isCustomModel && !isLocalMode() && 
        !canAccessModel(subscriptionStatus, modelOption?.requiresSubscription ?? false)) {
      console.warn('Model not accessible:', modelId);
      return;
    }
    console.log('setting selected model', modelId);
    setSelectedModel(modelId);
    saveModelPreference(modelId);
  };

  // Get the actual model ID to send to the backend
  const getActualModelId = (modelId: string): string => {
    // No need for automatic prefixing in most cases - just return as is
    return modelId;
  };

  return {
    selectedModel,
    setSelectedModel: (modelId: string) => {
      handleModelChange(modelId);
    },
    subscriptionStatus,
    availableModels,
    allModels: MODEL_OPTIONS,  // Already pre-sorted
    customModels,
    getActualModelId,
    refreshCustomModels,
    // canAccessModel: (modelId: string) => { // Original canAccessModel replaced by canAccessModelCheck call below
    //   if (isLocalMode()) return true;
    //   const model = MODEL_OPTIONS.find(m => m.id === modelId);
    //   return model ? canAccessModel(subscriptionStatus, model.requiresSubscription) : false;
    // },
    canAccessModel: (modelId: string) => canAccessModelCheck(modelId, subscriptionStatus, MODEL_OPTIONS), // Updated to use the new check
    isSubscriptionRequired: (modelId: string) => {
      return MODEL_OPTIONS.find(m => m.id === modelId)?.requiresSubscription || false;
    }
  };
};

// Export the hook but not any sorting logic - sorting is handled internally