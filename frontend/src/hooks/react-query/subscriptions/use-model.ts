import { createQueryHook } from "@/hooks/use-query";
import { AvailableModelsResponse, getAvailableModels } from "@/lib/api";
import { modelKeys } from "./keys";

export const useAvailableModels = createQueryHook<AvailableModelsResponse, Error>(
    modelKeys.available,
    getAvailableModels,
    {
      staleTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 2,
      select: (data) => {
        return {
          ...data,
          models: [...data.models].sort((a, b) => 
            a.display_name.localeCompare(b.display_name)
          ),
        };
      },
    }
  );

import { useQuery } from '@tanstack/react-query';
import { getAllLlmModels, AllLlmModelsResponse } from '@/lib/api'; // Ensure this path is correct

export const useAllLlmModels = () => {
  return useQuery<AllLlmModelsResponse, Error>({
    queryKey: ['allLlmModels'], // queryKeys.allLlmModels() could be added to keys.ts if preferred
    queryFn: getAllLlmModels,
    staleTime: 1000 * 60 * 5, // Cache for 5 minutes
    refetchOnWindowFocus: false, // Optional: prevent refetch on window focus
  });
};