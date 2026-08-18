build-Function:
	pip install . --target "$(ARTIFACTS_DIR)"
	rm -rf "$(ARTIFACTS_DIR)/boto3" "$(ARTIFACTS_DIR)/botocore" "$(ARTIFACTS_DIR)/s3transfer"
