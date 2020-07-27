"""SageMaker component for training."""
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Dict, Type

from train.src.sagemaker_training_spec import SageMakerTrainingSpec
from common.sagemaker_component import (
    SageMakerComponent,
    ComponentMetadata,
    SageMakerJobStatus,
)


@ComponentMetadata(
    name="SageMaker - Training Job",
    description="Train Machine Learning and Deep Learning Models using SageMaker",
    spec=SageMakerTrainingSpec,
)
class SageMakerTrainingComponent(SageMakerComponent):
    """SageMaker component for training."""

    def Do(self, spec: SageMakerTrainingSpec):
        self._training_job_name = (
            spec.inputs.get("job_name")
            if spec.inputs.get("job_name")
            else SageMakerComponent._generate_unique_timestamped_id(
                prefix="TrainingJob"
            )
        )
        super().Do(spec)

    def _get_job_status(self) -> SageMakerJobStatus:
        response = self._sm_client.describe_training_job(
            TrainingJobName=self._training_job_name
        )
        status = response["TrainingJobStatus"]

        if status == "Completed":
            return SageMakerJobStatus(is_completed=True, has_error=False)
        if status == "Failed":
            message = response["FailureReason"]
            return SageMakerJobStatus(
                is_completed=True, has_error=True, error_message=message
            )

        return SageMakerJobStatus(is_completed=False)

    def _after_job_complete(
        self, job: object, request: Dict, spec: SageMakerTrainingSpec
    ):
        spec.outputs["job_name"] = self._training_job_name
        spec.outputs["model_artifact_url"] = self._get_model_artifacts_from_job()
        spec.outputs["training_image"] = self._get_image_from_job()

    def _on_job_terminated(self):
        self._sm_client.stop_training_job(TrainingJobName=job_name)

    def _print_logs_for_job(self):
        self._print_cloudwatch_logs("/aws/sagemaker/TrainingJobs", job_name)

    def _create_job_request(self, spec: SageMakerTrainingSpec) -> Dict:
        ### Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sagemaker.html#SageMaker.Client.create_training_job
        request = self._get_request_template("train")

        request["TrainingJobName"] = self._training_job_name
        request["RoleArn"] = spec.inputs.get("role")
        request["HyperParameters"] = self._create_hyperparameters(
            spec.inputs.get("hyperparameters")
        )
        request["AlgorithmSpecification"]["TrainingInputMode"] = spec.inputs.get(
            "training_input_mode"
        )

        ### Update training image (for BYOC and built-in algorithms) or algorithm resource name
        if not spec.inputs.get("image") and not spec.inputs.get("algorithm_name"):
            logging.error("Please specify training image or algorithm name.")
            raise Exception("Could not create job request")
        if spec.inputs.get("image") and spec.inputs.get("algorithm_name"):
            logging.error(
                "Both image and algorithm name inputted, only one should be specified. Proceeding with image."
            )

        if spec.inputs.get("image"):
            request["AlgorithmSpecification"]["TrainingImage"] = spec.inputs.get(
                "image"
            )
            request["AlgorithmSpecification"].pop("AlgorithmName")
        else:
            # TODO: Adjust this implementation to account for custom algorithm resources names that are the same as built-in algorithm names
            algo_name = spec.inputs.get("algorithm_name").lower().strip()
            if algo_name in built_in_algos.keys():
                request["AlgorithmSpecification"]["TrainingImage"] = get_image_uri(
                    spec.inputs.get("region"), built_in_algos[algo_name]
                )
                request["AlgorithmSpecification"].pop("AlgorithmName")
                logging.warning(
                    "Algorithm name is found as an Amazon built-in algorithm. Using built-in algorithm."
                )
            # Just to give the user more leeway for built-in algorithm name inputs
            elif algo_name in built_in_algos.values():
                request["AlgorithmSpecification"]["TrainingImage"] = get_image_uri(
                    spec.inputs.get("region"), algo_name
                )
                request["AlgorithmSpecification"].pop("AlgorithmName")
                logging.warning(
                    "Algorithm name is found as an Amazon built-in algorithm. Using built-in algorithm."
                )
            else:
                request["AlgorithmSpecification"]["AlgorithmName"] = spec.inputs.get(
                    "algorithm_name"
                )
                request["AlgorithmSpecification"].pop("TrainingImage")

        ### Update metric definitions
        if spec.inputs.get("metric_definitions"):
            for key, val in spec.inputs.get("metric_definitions").items():
                request["AlgorithmSpecification"]["MetricDefinitions"].append(
                    {"Name": key, "Regex": val}
                )
        else:
            request["AlgorithmSpecification"].pop("MetricDefinitions")

        ### Update or pop VPC configs
        if spec.inputs.get("vpc_security_group_ids") and spec.inputs.get("vpc_subnets"):
            request["VpcConfig"]["SecurityGroupIds"] = spec.inputs.get(
                "vpc_security_group_ids"
            ).split(",")
            request["VpcConfig"]["Subnets"] = spec.inputs.get("vpc_subnets").split(",")
        else:
            request.pop("VpcConfig")

        ### Update input channels, must have at least one specified
        if len(spec.inputs.get("channels")) > 0:
            request["InputDataConfig"] = spec.inputs.get("channels")
        else:
            logging.error("Must specify at least one input channel.")
            raise Exception("Could not create job request")

        request["OutputDataConfig"]["S3OutputPath"] = spec.inputs.get(
            "model_artifact_path"
        )
        request["OutputDataConfig"]["KmsKeyId"] = spec.inputs.get(
            "output_encryption_key"
        )
        request["ResourceConfig"]["InstanceType"] = spec.inputs.get("instance_type")
        request["ResourceConfig"]["VolumeKmsKeyId"] = spec.inputs.get(
            "resource_encryption_key"
        )
        request["EnableNetworkIsolation"] = spec.inputs.get("network_isolation")
        request["EnableInterContainerTrafficEncryption"] = spec.inputs.get(
            "traffic_encryption"
        )

        ### Update InstanceCount, VolumeSizeInGB, and MaxRuntimeInSeconds if input is non-empty and > 0, otherwise use default values
        if spec.inputs.get("instance_count"):
            request["ResourceConfig"]["InstanceCount"] = spec.inputs.get(
                "instance_count"
            )

        if spec.inputs.get("volume_size"):
            request["ResourceConfig"]["VolumeSizeInGB"] = spec.inputs.get("volume_size")

        if spec.inputs.get("max_run_time"):
            request["StoppingCondition"]["MaxRuntimeInSeconds"] = spec.inputs.get(
                "max_run_time"
            )

        self._enable_spot_instance_support(request, spec)

        for key, val in spec.inputs.get("tags", {}).items():
            request["Tags"].append({"Key": key, "Value": val})

        return request

    def _submit_job_request(self, request: Dict):
        self._sm_client.create_training_job(**request)

    def _after_submit_job_request(self, spec: SageMakerTrainingSpec):
        logging.info(f"Created Training Job with name: {self._training_job_name}")
        logging.info(
            "Training job in SageMaker: https://{}.console.aws.amazon.com/sagemaker/home?region={}#/jobs/{}".format(
                spec.inputs.get("region"),
                spec.inputs.get("region"),
                self._training_job_name,
            )
        )
        logging.info(
            "CloudWatch logs: https://{}.console.aws.amazon.com/cloudwatch/home?region={}#logStream:group=/aws/sagemaker/TrainingJobs;prefix={};streamFilter=typeLogStreamPrefix".format(
                spec.inputs.get("region"),
                spec.inputs.get("region"),
                self._training_job_name,
            )
        )

    def _get_model_artifacts_from_job(self):
        info = self._sm_client.describe_training_job(
            TrainingJobName=self._training_job_name
        )
        model_artifact_url = info["ModelArtifacts"]["S3ModelArtifacts"]
        return model_artifact_url

    def _get_image_from_job(self):
        info = self._sm_client.describe_training_job(
            TrainingJobName=self._training_job_name
        )
        if "TrainingImage" in info["AlgorithmSpecification"]:
            image = info["AlgorithmSpecification"]["TrainingImage"]
        else:
            algorithm_name = info["AlgorithmSpecification"]["AlgorithmName"]
            image = self._sm_client.describe_algorithm(AlgorithmName=algorithm_name)[
                "TrainingSpecification"
            ]["TrainingImage"]

        return image


if __name__ == "__main__":
    import sys

    spec = SageMakerTrainingSpec(sys.argv[1:])

    component = SageMakerTrainingComponent()
    component.Do(spec)
